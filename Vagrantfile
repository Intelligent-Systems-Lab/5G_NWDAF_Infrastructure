# frozen_string_literal: true

require "ipaddr"
require "yaml"

ROOT = File.expand_path(__dir__)
definition_selection = ENV["TESTBED"]
abort "TESTBED must select an explicit testbed definition" if definition_selection.nil? || definition_selection.strip.empty?
definition_path = definition_selection
definition_path = File.expand_path(definition_path, ROOT)
abort "testbed definition not found: #{definition_path}" unless File.file?(definition_path)

testbed = YAML.safe_load(File.read(definition_path), aliases: false)
abort "unsupported testbed schema" unless testbed["schemaVersion"] == 1

legacy_local_path = File.join(ROOT, "testbed.local.yaml")
abort "testbed.local.yaml is no longer supported; move topology settings into #{definition_path}" if File.exist?(legacy_local_path)
requested_provider = ENV["VAGRANT_DEFAULT_PROVIDER"]
abort "unsupported provider #{requested_provider}; expected virtualbox" if requested_provider && requested_provider != "virtualbox"
provider_name = "virtualbox"

machines = testbed.fetch("machines")
networks = testbed.fetch("networks")
expected_names = %w[core path-a path-b]
abort "machines must be exactly #{expected_names.join(', ')}" unless machines.keys.sort == expected_names.sort

machines.each do |machine_name, machine|
  machine.fetch("interfaces").each do |network_name, address|
    network = networks.fetch(network_name) { abort "unknown network #{network_name}" }
    cidr = IPAddr.new(network.fetch("cidr"))
    abort "#{machine_name} #{address} is outside #{network_name}" unless cidr.include?(IPAddr.new(address))
  end
end

Vagrant.configure("2") do |config|
  config.vm.box = testbed.dig("guest", "box") || "ubuntu/jammy64"
  config.vm.box_version = testbed.dig("guest", "boxVersion") if testbed.dig("guest", "boxVersion")
  config.vm.box_check_update = false
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.vm.synced_folder ROOT, "/opt/5g-nwdaf-infrastructure/source",
    type: "rsync", rsync__auto: false,
    rsync__exclude: [
      ".git/", ".vagrant/", ".generated/", "ML/", "config/local/",
      "**/.venv/", "**/__pycache__/", "**/.pytest_cache/", "**/node_modules/"
    ]

  machines.each do |machine_name, machine|
    config.vm.define machine_name do |node|
      node.vm.hostname = "5g-nwdaf-#{machine_name}"

      machine.fetch("interfaces").each do |_network_name, address|
        node.vm.network "private_network", ip: address
      end

      resources = machine.fetch("resources")
      node.vm.disk :disk, size: "#{resources.fetch("diskGiB")}GB", primary: true
      node.vm.provider "virtualbox" do |vb|
        vb.name = "5g-nwdaf-#{machine_name}"
        vb.memory = resources.fetch("memoryMiB")
        vb.cpus = resources.fetch("cpus")
      end
      node.vm.provision "shell", path: "scripts/guest/common.sh",
        args: [machine_name], run: "once"
      if machine_name == "core"
        node.vm.provision "shell", path: "scripts/guest/core.sh", args: ["setup"], run: "once"
      else
        path_name = machine_name.end_with?("a") ? "A" : "B"
        node.vm.provision "shell", path: "scripts/guest/path.sh",
          args: [path_name, "setup"], run: "once"
      end

    end
  end

  config.vm.provider(provider_name) {} if provider_name
end
