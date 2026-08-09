// Scoped full-core subscriber/group provisioning for mongosh.
// The compact fixture schema is adapted from nwdaf-resources commit
// d2634b84e8790a6b696e5b21ec1a0f660b683948; the Mongo projection is owned here.

const fs = require("fs");

const action = process.env.ACTION || "show";
const subscriberPath = process.env.SUBSCRIBER_FIXTURE;
const groupPath = process.env.GROUP_FIXTURE;

function requireValue(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

requireValue(["validate", "plan", "apply", "show", "clear"].includes(action), "invalid action");
requireValue(subscriberPath && groupPath, "fixture paths are required");

const fixture = JSON.parse(fs.readFileSync(subscriberPath, "utf8"));
const groupFixture = JSON.parse(fs.readFileSync(groupPath, "utf8"));
const defaults = fixture.defaults;
const authentication = defaults.authentication;
const subscribers = fixture.subscribers;
const groups = groupFixture.groups;
const supis = subscribers.map(item => item.supi);
const groupIds = groups.map(item => item.intGroupId);

requireValue(fixture.schemaVersion === 1, "unsupported subscriber fixture schema");
requireValue(groupFixture.schemaVersion === 1, "unsupported group fixture schema");
requireValue(subscribers.length === 6, "full-core fixture must contain six subscribers");
requireValue(new Set(supis).size === supis.length, "subscriber SUPIs must be unique");
requireValue(groups.length === 1, "full-core fixture must contain one Internal Group");
requireValue(
  JSON.stringify(groups[0].ueIdList.map(item => item.supi).sort()) === JSON.stringify([...supis].sort()),
  "Internal Group membership must match subscriber SUPIs"
);

const snssai = defaults.snssai;
const snssaiKey = snssai.sst.toString(16).padStart(2, "0") + snssai.sd;
const documents = [];

for (const subscriber of subscribers) {
  const supi = subscriber.supi;
  const gpsi = subscriber.gpsi;
  const ueKey = {ueId: supi};
  const plmnKey = {ueId: supi, servingPlmnId: fixture.servingPlmnId};
  const authDocument = {
    ueId: supi,
    authenticationMethod: authentication.authenticationMethod,
    encPermanentKey: authentication.permanentKeyValue,
    encOpcKey: authentication.opcValue,
    sequenceNumber: {sqnScheme: "GENERAL", sqn: authentication.sequenceNumber},
    authenticationManagementField: authentication.authenticationManagementField
  };
  const webAuthDocument = {
    ueId: supi,
    authenticationMethod: authentication.authenticationMethod,
    sequenceNumber: authentication.sequenceNumber,
    authenticationManagementField: authentication.authenticationManagementField,
    permanentKey: {
      permanentKeyValue: authentication.permanentKeyValue,
      encryptionKey: 0,
      encryptionAlgorithm: 0
    },
    milenage: {op: {opValue: "", encryptionKey: 0, encryptionAlgorithm: 0}},
    opc: {opcValue: authentication.opcValue, encryptionKey: 0, encryptionAlgorithm: 0}
  };
  const amDocument = {
    ...plmnKey,
    gpsis: [gpsi],
    subscribedUeAmbr: defaults.subscribedUeAmbr,
    nssai: {defaultSingleNssais: [snssai], singleNssais: []}
  };
  const smDocument = {
    ...plmnKey,
    singleNssai: snssai,
    dnnConfigurations: {
      [defaults.dnn]: {
        pduSessionTypes: {
          defaultSessionType: defaults.pduSessionType,
          allowedSessionTypes: [defaults.pduSessionType]
        },
        sscModes: {defaultSscMode: defaults.sscMode, allowedSscModes: [defaults.sscMode]},
        "5gQosProfile": {
          "5qi": defaults.fiveQi,
          arp: {priorityLevel: defaults.priorityLevel},
          priorityLevel: defaults.priorityLevel
        },
        sessionAmbr: defaults.sessionAmbr,
        staticIpAddress: []
      }
    }
  };
  const smfSelectionDocument = {
    ...plmnKey,
    subscribedSnssaiInfos: {[snssaiKey]: {dnnInfos: [{dnn: defaults.dnn}]}}
  };
  const smPolicyDocument = {
    ueId: supi,
    smPolicySnssaiData: {
      [snssaiKey]: {
        snssai: snssai,
        smPolicyDnnData: {[defaults.dnn]: {dnn: defaults.dnn}}
      }
    }
  };

  documents.push(
    ["subscriptionData.authenticationData.webAuthenticationSubscription", ueKey, webAuthDocument],
    ["subscriptionData.authenticationData.authenticationSubscription", ueKey, authDocument],
    ["subscriptionData.provisionedData.amData", plmnKey, amDocument],
    ["subscriptionData.provisionedData.smData", {...plmnKey, singleNssai: snssai}, smDocument],
    ["subscriptionData.provisionedData.smfSelectionSubscriptionData", plmnKey, smfSelectionDocument],
    ["policyData.ues.amData", ueKey, {ueId: supi, subscCats: ["free5gc"]}],
    ["policyData.ues.smData", ueKey, smPolicyDocument],
    ["subscriptionData.identityData", ueKey, {ueId: supi, gpsi: gpsi}]
  );
}

const subscriberCollections = [...new Set(documents.map(item => item[0]))].sort();
const groupCollection = "subscriptionData.groupData.groupIdentifiers";

if (action === "validate") {
  print(JSON.stringify({subscribers: supis.length, documents: documents.length, groups: groups.length}));
} else if (action === "plan") {
  const counts = {};
  for (const collection of subscriberCollections) {
    counts[collection] = documents.filter(item => item[0] === collection).length;
  }
  print(JSON.stringify({database: db.getName(), collections: counts, groupIds: groupIds}, null, 2));
} else if (action === "apply") {
  for (const [collection, key, document] of documents) {
    db.getCollection(collection).replaceOne(key, document, {upsert: true});
  }
  for (const group of groups) {
    db.getCollection(groupCollection).replaceOne({intGroupId: group.intGroupId}, group, {upsert: true});
  }
  print(JSON.stringify({appliedSubscriberDocuments: documents.length, appliedGroups: groups.length}));
} else if (action === "show") {
  const counts = {};
  for (const collection of subscriberCollections) {
    counts[collection] = db.getCollection(collection).countDocuments({ueId: {$in: supis}});
  }
  const storedGroups = db.getCollection(groupCollection)
    .find({intGroupId: {$in: groupIds}}, {_id: 0}).toArray();
  print(JSON.stringify({database: db.getName(), collections: counts, groups: storedGroups}, null, 2));
} else if (action === "clear") {
  let removedSubscriberDocuments = 0;
  for (const collection of subscriberCollections) {
    removedSubscriberDocuments += db.getCollection(collection).deleteMany({ueId: {$in: supis}}).deletedCount;
  }
  const removedGroups = db.getCollection(groupCollection)
    .deleteMany({intGroupId: {$in: groupIds}}).deletedCount;
  print(JSON.stringify({removedSubscriberDocuments: removedSubscriberDocuments, removedGroups: removedGroups}));
}
