// Scoped experiment-state reset for mongosh. Never drops a database or collection.

const action = process.env.ACTION;
const adrfDatabaseName = process.env.ADRF_DATABASE;
const expectedInstanceId = process.env.ADRF_INSTANCE_ID;
const nrfCollections = (process.env.NRF_COLLECTIONS || "").split(",").filter(Boolean);
const nrfNfType = process.env.NRF_NF_TYPE;
const nrfInstanceIds = (process.env.NRF_INSTANCE_IDS || "").split(",")
  .filter(value => value && value !== "-");
const adrfCollections = (process.env.ADRF_COLLECTIONS || "").split(",").filter(Boolean);

if (!["plan", "apply", "verify"].includes(action)) {
  throw new Error("ACTION must be plan, apply, or verify");
}
if (!adrfDatabaseName || !expectedInstanceId || (!nrfInstanceIds.length && (!nrfNfType || nrfNfType === "-"))) {
  throw new Error("reset identity inputs are required");
}
if (JSON.stringify(nrfCollections) !== JSON.stringify(["NfProfile", "urilist"])) {
  throw new Error("unexpected NRF reset collection scope");
}
if (JSON.stringify(adrfCollections) !== JSON.stringify(["data_store_records", "mlmodel_store_records"])) {
  throw new Error("unexpected ADRF reset collection scope");
}

const nrfDatabase = db;
const adrfDatabase = db.getSiblingDB(adrfDatabaseName);
const profileFilter = nrfInstanceIds.length
  ? {nfInstanceId: {$in: nrfInstanceIds}}
  : {nfType: nrfNfType};

function selectedUriState() {
  if (!nrfInstanceIds.length) {
    return nrfDatabase.getCollection("urilist").countDocuments({nfType: nrfNfType});
  }
  const selected = new Set(nrfInstanceIds);
  return nrfDatabase.getCollection("urilist").find({}).toArray()
    .flatMap(item => (((item || {})._link || {}).item || []))
    .map(item => item.href || "")
    .filter(href => Array.from(selected).some(id => href.endsWith("/" + id)));
}

function snapshot() {
  const profiles = nrfDatabase.getCollection("NfProfile")
    .find(profileFilter, {_id: 0, nfInstanceId: 1})
    .toArray()
    .map(item => item.nfInstanceId)
    .sort();
  const records = {};
  for (const collection of adrfCollections) {
    records[collection] = adrfDatabase.getCollection(collection).countDocuments({});
  }
  return {
    configuredAdrfInstanceId: expectedInstanceId,
    nrf: {
      database: nrfDatabase.getName(),
      adrfProfileIds: profiles,
      adrfProfileCount: profiles.length,
      selectedUriList: selectedUriState(),
      selectedUriListCount: nrfInstanceIds.length
        ? selectedUriState().length
        : selectedUriState()
    },
    adrf: {database: adrfDatabaseName, collections: records}
  };
}

const before = snapshot();
if (action === "plan") {
  print(JSON.stringify({action: action, state: before}, null, 2));
} else if (action === "apply") {
  const selectedUris = nrfInstanceIds.length ? before.nrf.selectedUriList : [];
  const selectedUriDocumentIds = nrfInstanceIds.length
    ? nrfDatabase.getCollection("urilist")
      .find({"_link.item.href": {$in: selectedUris}}, {_id: 1})
      .toArray()
      .map(item => item._id)
    : [];
  const removed = {
    nrfProfiles: nrfDatabase.getCollection("NfProfile").deleteMany(profileFilter).deletedCount,
    nrfUriLists: 0,
    adrfCollections: {}
  };
  if (nrfInstanceIds.length) {
    const result = nrfDatabase.getCollection("urilist").updateMany(
      {_id: {$in: selectedUriDocumentIds}},
      {$pull: {"_link.item": {href: {$in: selectedUris}}}}
    );
    removed.nrfUriLists = result.modifiedCount;
    nrfDatabase.getCollection("urilist").deleteMany({
      _id: {$in: selectedUriDocumentIds}, "_link.item.0": {$exists: false}
    });
  } else {
    removed.nrfUriLists = nrfDatabase.getCollection("urilist")
      .deleteMany({nfType: nrfNfType}).deletedCount;
  }
  for (const collection of adrfCollections) {
    removed.adrfCollections[collection] = adrfDatabase
      .getCollection(collection).deleteMany({}).deletedCount;
  }
  print(JSON.stringify({action: action, before: before, removed: removed, after: snapshot()}, null, 2));
} else {
  const state = snapshot();
  print(JSON.stringify({action: action, state: state}, null, 2));
  const remainingRecords = Object.values(state.adrf.collections)
    .reduce((total, count) => total + count, 0);
  if (state.nrf.adrfProfileCount || state.nrf.selectedUriListCount || remainingRecords) {
    throw new Error("guest experiment state is not empty");
  }
}
