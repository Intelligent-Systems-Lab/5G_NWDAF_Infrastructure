package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"

	"github.com/parquet-go/parquet-go"
)

type resolvedProfile struct {
	SchemaVersion                  int      `json:"schemaVersion"`
	Path                           string   `json:"path"`
	WindowSeconds                  int      `json:"windowSeconds"`
	BreakingTimeSeconds            int      `json:"breakingTimeSeconds"`
	StableWindows                  int      `json:"stableWindows"`
	DegradedWindows                int      `json:"degradedWindows"`
	PostBoundaryMode               string   `json:"postBoundaryMode"`
	StableUplinkBytes              int64    `json:"stableUplinkBytes"`
	StableDownlinkBytes            int64    `json:"stableDownlinkBytes"`
	DegradedUplinkBytes            int64    `json:"degradedUplinkBytes"`
	DegradedDownlinkBytes          int64    `json:"degradedDownlinkBytes"`
	DegradedJitterScale            int64    `json:"degradedJitterScale"`
	UEIPs                          []string `json:"ueIps"`
	ArtifactFile                   string   `json:"artifactFile"`
	GuestDirectory                 string   `json:"guestDirectory"`
	ProfileSource                  string   `json:"profileSource"`
	ProfileHash                    string   `json:"profileHash"`
	SamplingIntervalSeconds        int      `json:"samplingIntervalSeconds"`
	ModelInputWindow               int      `json:"modelInputWindow"`
	ModelOutputWindow              int      `json:"modelOutputWindow"`
	ValidationRatio                float64  `json:"validationRatio"`
	HistoricalObservations         int      `json:"historicalObservations"`
	MinimumPreparationObservations int      `json:"minimumPreparationObservations"`
	MinimumTrainingSamples         int      `json:"minimumTrainingSamples"`
	MinimumValidationSamples       int      `json:"minimumValidationSamples"`
	TrainingSamples                int      `json:"trainingSamples"`
	ValidationSamples              int      `json:"validationSamples"`
	MonitorReportPeriodSeconds     int      `json:"monitorReportPeriodSeconds"`
	MinimumReferenceReports        int      `json:"minimumReferenceReports"`
	RequiredDegradationHits        int      `json:"requiredDegradationHits"`
	StableLeadInSeconds            int      `json:"stableLeadInSeconds"`
	DegradedTailSeconds            int      `json:"degradedTailSeconds"`
}

type setSpec struct {
	SchemaVersion       int                        `json:"schemaVersion"`
	DatasetSetID        string                     `json:"datasetSetId"`
	GeneratorSourceHash string                     `json:"generatorSourceHash"`
	Paths               map[string]resolvedProfile `json:"paths"`
}

type row struct {
	Timestamp float64 `parquet:"ts"`
	Direction string  `parquet:"direction"`
	Length    int64   `parquet:"len"`
	Action    string  `parquet:"action"`
	UEIP      string  `parquet:"ue_ip"`
}

type pathManifest struct {
	SchemaVersion int             `json:"schemaVersion"`
	DatasetSetID  string          `json:"datasetSetId"`
	Path          string          `json:"path"`
	ArtifactFile  string          `json:"artifactFile"`
	SHA256        string          `json:"sha256"`
	Bytes         int64           `json:"bytes"`
	Rows          int64           `json:"rows"`
	MinTimestamp  float64         `json:"minTimestamp"`
	MaxTimestamp  float64         `json:"maxTimestamp"`
	UEIPs         []string        `json:"ueIps"`
	Profile       resolvedProfile `json:"profile"`
}

type setManifest struct {
	SchemaVersion       int                     `json:"schemaVersion"`
	DatasetSetID        string                  `json:"datasetSetId"`
	GeneratorSourceHash string                  `json:"generatorSourceHash"`
	Paths               map[string]pathManifest `json:"paths"`
}

func main() {
	if len(os.Args) != 4 || (os.Args[1] != "generate" && os.Args[1] != "check") {
		fatalf("usage: datasetgen generate|check SPEC.json OUTPUT_ROOT")
	}
	spec := readSpec(os.Args[2])
	var err error
	if os.Args[1] == "generate" {
		err = generate(spec, os.Args[3])
	} else {
		err = check(spec, os.Args[3])
	}
	if err != nil {
		fatalf("%v", err)
	}
	fmt.Printf("OK dataset=%s root=%s\n", spec.DatasetSetID, os.Args[3])
}

func readSpec(name string) setSpec {
	data, err := os.ReadFile(name)
	if err != nil {
		fatalf("read spec: %v", err)
	}
	var spec setSpec
	if err := json.Unmarshal(data, &spec); err != nil {
		fatalf("decode spec: %v", err)
	}
	if spec.SchemaVersion != 1 || spec.DatasetSetID == "" || len(spec.Paths) != 2 {
		fatalf("invalid dataset set specification")
	}
	return spec
}

func generate(spec setSpec, root string) error {
	if entries, err := os.ReadDir(root); err != nil || len(entries) != 0 {
		if err != nil {
			return fmt.Errorf("read output root: %w", err)
		}
		return errors.New("output root must be empty")
	}
	manifest := setManifest{1, spec.DatasetSetID, spec.GeneratorSourceHash, map[string]pathManifest{}}
	keys := sortedKeys(spec.Paths)
	for _, key := range keys {
		profile := spec.Paths[key]
		pathRoot := filepath.Join(root, key)
		if err := os.Mkdir(pathRoot, 0o755); err != nil {
			return err
		}
		artifact := filepath.Join(pathRoot, profile.ArtifactFile)
		if err := writeParquet(artifact, profile); err != nil {
			return fmt.Errorf("generate %s: %w", key, err)
		}
		meta := map[string]int{
			"breaking time":          profile.BreakingTimeSeconds,
			"total duration seconds": (profile.StableWindows + profile.DegradedWindows) * profile.WindowSeconds,
		}
		if err := writeJSON(filepath.Join(pathRoot, "file.json"), meta); err != nil {
			return err
		}
		audit, err := auditParquet(artifact, profile)
		if err != nil {
			return err
		}
		audit.SchemaVersion = 1
		audit.DatasetSetID = spec.DatasetSetID
		audit.Path = key
		audit.ArtifactFile = profile.ArtifactFile
		audit.Profile = profile
		manifest.Paths[key] = audit
		if err := writeJSON(filepath.Join(pathRoot, "manifest.json"), audit); err != nil {
			return err
		}
	}
	return writeJSON(filepath.Join(root, "manifest.json"), manifest)
}

func writeParquet(name string, profile resolvedProfile) error {
	file, err := os.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	writer := parquet.NewGenericWriter[row](file)
	total := profile.StableWindows + profile.DegradedWindows
	buffer := make([]row, 0, 2048)
	flush := func() error {
		if len(buffer) == 0 {
			return nil
		}
		_, err := writer.Write(buffer)
		buffer = buffer[:0]
		return err
	}
	for window := 0; window < total; window++ {
		uplink := profile.StableUplinkBytes + int64(window%5)
		downlink := profile.StableDownlinkBytes + int64(window%7)
		if window >= profile.StableWindows && profile.PostBoundaryMode == "degraded" {
			uplink = profile.DegradedUplinkBytes + int64(window%5)*profile.DegradedJitterScale
			downlink = profile.DegradedDownlinkBytes + int64(window%7)*profile.DegradedJitterScale
		}
		for index, ueIP := range profile.UEIPs {
			timestamp := float64(window * profile.WindowSeconds)
			buffer = append(buffer,
				row{timestamp, "0", uplink + int64(index), "allow", ueIP},
				row{timestamp, "1", downlink + int64(index), "allow", ueIP},
			)
			if len(buffer) >= 2048 {
				if err := flush(); err != nil {
					return err
				}
			}
		}
	}
	if err := flush(); err != nil {
		return err
	}
	if err := writer.Close(); err != nil {
		return err
	}
	return file.Close()
}

func auditParquet(name string, profile resolvedProfile) (pathManifest, error) {
	var result pathManifest
	artifact, err := os.Open(name)
	if err != nil {
		return result, err
	}
	digest := sha256.New()
	result.Bytes, err = io.Copy(digest, artifact)
	if closeErr := artifact.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return result, err
	}
	result.SHA256 = hex.EncodeToString(digest.Sum(nil))
	file, err := os.Open(name)
	if err != nil {
		return result, err
	}
	defer file.Close()
	reader := parquet.NewGenericReader[row](file)
	defer reader.Close()
	first := true
	buffer := make([]row, 1024)
	for {
		n, readErr := reader.Read(buffer)
		for _, item := range buffer[:n] {
			window := int(result.Rows) / (len(profile.UEIPs) * 2)
			offset := int(result.Rows) % (len(profile.UEIPs) * 2)
			ueIndex := offset / 2
			direction := fmt.Sprintf("%d", offset%2)
			uplink := profile.StableUplinkBytes + int64(window%5)
			downlink := profile.StableDownlinkBytes + int64(window%7)
			if window >= profile.StableWindows && profile.PostBoundaryMode == "degraded" {
				uplink = profile.DegradedUplinkBytes + int64(window%5)*profile.DegradedJitterScale
				downlink = profile.DegradedDownlinkBytes + int64(window%7)*profile.DegradedJitterScale
			}
			expectedLength := uplink + int64(ueIndex)
			if direction == "1" {
				expectedLength = downlink + int64(ueIndex)
			}
			if item.UEIP != profile.UEIPs[ueIndex] || item.Direction != direction ||
				item.Timestamp != float64(window*profile.WindowSeconds) || item.Action != "allow" ||
				item.Length != expectedLength {
				return result, fmt.Errorf("%s row %d does not match resolved traffic profile", name, result.Rows)
			}
			if first || item.Timestamp < result.MinTimestamp {
				result.MinTimestamp = item.Timestamp
			}
			if first || item.Timestamp > result.MaxTimestamp {
				result.MaxTimestamp = item.Timestamp
			}
			first = false
			result.Rows++
		}
		if errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil {
			return result, fmt.Errorf("read %s: %w", name, readErr)
		}
	}
	expectedRows := int64((profile.StableWindows + profile.DegradedWindows) * len(profile.UEIPs) * 2)
	if result.Rows != expectedRows {
		return result, fmt.Errorf("%s has %d rows, expected %d", name, result.Rows, expectedRows)
	}
	if result.MinTimestamp != 0 || result.MaxTimestamp != float64((profile.StableWindows+profile.DegradedWindows-1)*profile.WindowSeconds) {
		return result, fmt.Errorf("%s timestamp range is %.0f..%.0f", name, result.MinTimestamp, result.MaxTimestamp)
	}
	result.UEIPs = append([]string(nil), profile.UEIPs...)
	return result, nil
}

func check(spec setSpec, root string) error {
	var manifest setManifest
	if err := readJSON(filepath.Join(root, "manifest.json"), &manifest); err != nil {
		return err
	}
	if manifest.SchemaVersion != 1 || manifest.DatasetSetID != spec.DatasetSetID || manifest.GeneratorSourceHash != spec.GeneratorSourceHash {
		return errors.New("dataset set manifest identity does not match resolved specification")
	}
	if len(manifest.Paths) != len(spec.Paths) {
		return errors.New("dataset set manifest path count does not match specification")
	}
	for _, key := range sortedKeys(spec.Paths) {
		profile := spec.Paths[key]
		var recorded pathManifest
		if err := readJSON(filepath.Join(root, key, "manifest.json"), &recorded); err != nil {
			return err
		}
		actual, err := auditParquet(filepath.Join(root, key, profile.ArtifactFile), profile)
		if err != nil {
			return err
		}
		if recorded.DatasetSetID != spec.DatasetSetID || recorded.Path != key || recorded.ArtifactFile != profile.ArtifactFile ||
			recorded.SHA256 != actual.SHA256 || recorded.Bytes != actual.Bytes || recorded.Rows != actual.Rows ||
			recorded.MinTimestamp != actual.MinTimestamp || recorded.MaxTimestamp != actual.MaxTimestamp ||
			!reflect.DeepEqual(recorded.Profile, profile) {
			return fmt.Errorf("%s artifact manifest does not match Parquet content", key)
		}
		if manifest.Paths[key].SHA256 != recorded.SHA256 || manifest.Paths[key].Rows != recorded.Rows {
			return fmt.Errorf("%s set manifest does not match path manifest", key)
		}
		var meta map[string]int
		if err := readJSON(filepath.Join(root, key, "file.json"), &meta); err != nil {
			return err
		}
		if meta["breaking time"] != profile.BreakingTimeSeconds {
			return fmt.Errorf("%s breaking time does not match profile", key)
		}
		if meta["total duration seconds"] != (profile.StableWindows+profile.DegradedWindows)*profile.WindowSeconds {
			return fmt.Errorf("%s total duration does not match profile", key)
		}
	}
	return nil
}

func sortedKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func writeJSON(name string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(name, data, 0o644)
}

func readJSON(name string, value any) error {
	data, err := os.ReadFile(name)
	if err != nil {
		return fmt.Errorf("read %s: %w", name, err)
	}
	if err := json.Unmarshal(data, value); err != nil {
		return fmt.Errorf("decode %s: %w", name, err)
	}
	return nil
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "ERROR: "+format+"\n", args...)
	os.Exit(1)
}
