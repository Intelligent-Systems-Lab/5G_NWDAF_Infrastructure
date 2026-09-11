#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

function valuesAtPath(document, dottedPath) {
  function descend(value, parts) {
    if (!parts.length) {
      return Array.isArray(value) ? value : [value];
    }
    const [part, ...remaining] = parts;
    if (Array.isArray(value)) {
      if (/^[0-9]+$/.test(part)) {
        const index = Number(part);
        return index < value.length ? descend(value[index], remaining) : [];
      }
      return value.flatMap(item => descend(item, parts));
    }
    if (!value || value[part] === undefined) {
      return [];
    }
    return descend(value[part], remaining);
  }

  return descend(document, dottedPath.split("."));
}

function matches(document, filter) {
  return Object.entries(filter).every(([key, condition]) => {
    const values = valuesAtPath(document, key);
    if (condition && Object.hasOwn(condition, "$in")) {
      return values.some(value => condition.$in.includes(value));
    }
    if (condition && Object.hasOwn(condition, "$exists")) {
      return (values.length > 0) === condition.$exists;
    }
    return values.includes(condition);
  });
}

class Collection {
  constructor(documents) {
    this.documents = structuredClone(documents);
  }

  find(filter) {
    return {toArray: () => this.documents.filter(document => matches(document, filter))};
  }

  countDocuments(filter) {
    return this.documents.filter(document => matches(document, filter)).length;
  }

  deleteMany(filter) {
    const before = this.documents.length;
    this.documents = this.documents.filter(document => !matches(document, filter));
    return {deletedCount: before - this.documents.length};
  }

  updateMany(filter, update) {
    let modifiedCount = 0;
    for (const document of this.documents) {
      if (!matches(document, filter)) {
        continue;
      }
      const rejected = new Set(update.$pull["_link.item"].href.$in);
      const before = document._link.item.length;
      document._link.item = document._link.item.filter(item => !rejected.has(item.href));
      if (document._link.item.length !== before) {
        modifiedCount += 1;
      }
    }
    return {modifiedCount};
  }
}

class Database {
  constructor(name, collections, siblings) {
    this.name = name;
    this.collections = collections;
    this.siblings = siblings;
  }

  getName() {
    return this.name;
  }

  getCollection(name) {
    return this.collections[name];
  }

  getSiblingDB(name) {
    return this.siblings[name];
  }
}

const selectedNwdaf = "10000000-0000-4000-8000-000000000001";
const selectedAdrf = "a0494f77-36db-4639-9f6f-e3e71fb5bfe6";
const unrelated = "20000000-0000-4000-8000-000000000001";
const urilist = new Collection([
  {
    _id: 1,
    nfType: "NWDAF",
    _link: {item: [
      {href: `http://nrf/nnrf-nfm/v1/nf-instances/${selectedNwdaf}`},
      {href: `http://nrf/nnrf-nfm/v1/nf-instances/${unrelated}`},
    ]},
  },
  {
    _id: 2,
    nfType: "ADRF",
    _link: {item: [{href: `http://nrf/nnrf-nfm/v1/nf-instances/${selectedAdrf}`}]},
  },
]);
const adrf = new Database("adrf", {
  data_store_records: new Collection([]),
  mlmodel_store_records: new Collection([]),
}, {});
global.db = new Database("free5gc", {
  NfProfile: new Collection([]),
  urilist,
}, {adrf});
global.print = () => {};

Object.assign(process.env, {
  ACTION: "apply",
  ADRF_DATABASE: "adrf",
  ADRF_INSTANCE_ID: selectedAdrf,
  NRF_COLLECTIONS: "NfProfile,urilist",
  NRF_NF_TYPE: "-",
  NRF_INSTANCE_IDS: `${selectedNwdaf},${selectedAdrf}`,
  ADRF_COLLECTIONS: "data_store_records,mlmodel_store_records",
});

require(path.resolve(__dirname, "../scripts/guest/experiment-reset.js"));

const remainingUris = urilist.documents
  .flatMap(document => document._link.item)
  .map(item => item.href);
assert.deepEqual(remainingUris, [
  `http://nrf/nnrf-nfm/v1/nf-instances/${unrelated}`,
]);
assert.equal(urilist.documents.length, 1);
console.log("EXPERIMENT_RESET_TEST status=passed stale_profile_uri_cleanup=exact");
