// Scoped experiment-state reset for mongosh. Never drops a database or collection.

const action = process.env.ACTION;
const adrfDatabaseName = process.env.ADRF_DATABASE;
const expectedInstanceId = process.env.ADRF_INSTANCE_ID;

if (!["plan", "apply", "verify"].includes(action)) {
  throw new Error("ACTION must be plan, apply, or verify");
}
if (!adrfDatabaseName || !expectedInstanceId) {
  throw new Error("ADRF_DATABASE and ADRF_INSTANCE_ID are required");
}

const nrfDatabase = db;
const adrfDatabase = db.getSiblingDB(adrfDatabaseName);
const profileFilter = {nfType: "ADRF"};
const uriListFilter = {nfType: "ADRF"};
const adrfCollections = ["data_store_records", "mlmodel_store_records"];

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
      adrfUriListCount: nrfDatabase.getCollection("urilist").countDocuments(uriListFilter)
    },
    adrf: {database: adrfDatabaseName, collections: records}
  };
}

const before = snapshot();
if (action === "plan") {
  print(JSON.stringify({action: action, state: before}, null, 2));
} else if (action === "apply") {
  const removed = {
    nrfProfiles: nrfDatabase.getCollection("NfProfile").deleteMany(profileFilter).deletedCount,
    nrfUriLists: nrfDatabase.getCollection("urilist").deleteMany(uriListFilter).deletedCount,
    adrfCollections: {}
  };
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
  if (state.nrf.adrfProfileCount || state.nrf.adrfUriListCount || remainingRecords) {
    throw new Error("guest experiment state is not empty");
  }
}
