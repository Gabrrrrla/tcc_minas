// LAB (UNISINOS): same as provision.js, but with the SD that the lab gNB advertises
// (SST 1 / SD 111111, SST 2 / SD 222222). Used only by docker-compose.lab.yml.
//
// The REAL IMSI/K/OPc (lab SIM cards) are in scripts/sims.lab.js, which
// is NOT versioned (.gitignore; the repository is public). To create it:
//   cp scripts/sims.lab.example.js scripts/sims.lab.js && nano scripts/sims.lab.js
// Without that file, the TEST credentials below are registered (useful only without radio).
//
// UE 1 (UE-A): subscribed to both slices — SST=1 (eMBB, DNN internet, default) and SST=2 (DNN slice2)
// UE 2 (UE-B): subscribed only to SST=2 (DNN slice2, mission-critical slice, UC1)
//
// Register again after changing the SIM cards:
//   docker compose -f docker-compose.yml -f docker-compose.lab.yml up provision

var SIMS = {
  ue1: { imsi: '001010000000001', k: '465B5CE8B199B49FAA5F0A2EE238A6BC', opc: 'E8ED289DEBA952E4283B54E88E6183CA' },
  ue2: { imsi: '001010000000002', k: '465B5CE8B199B49FAA5F0A2EE238A6BC', opc: 'E8ED289DEBA952E4283B54E88E6183CA' }
};
try {
  load('/scripts/sims.lab.js');   // redefines SIMS with the real SIM cards
  print('SIMs from scripts/sims.lab.js');
} catch (e) {
  print('WARNING: scripts/sims.lab.js not found or invalid (' + e + ') -> TEST credentials (no real phone will attach)');
}

db = connect('mongodb://mongo/open5gs');

function session(name) {
  return {
    name: name,
    type: 3,
    qos: {
      index: 9,
      arp: { priority_level: 8, pre_emption_capability: 1, pre_emption_vulnerability: 1 }
    },
    ambr: {
      downlink: { value: NumberInt(1), unit: NumberInt(3) },
      uplink:   { value: NumberInt(1), unit: NumberInt(3) }
    },
    pcc_rule: [],
    _id: new ObjectId()
  };
}

function slice(sst, sd, dnn, isDefault) {
  return { sst: sst, sd: sd, default_indicator: isDefault, session: [session(dnn)], _id: new ObjectId() };
}

function provision(sim, slices, what) {
  var imsi = String(sim.imsi).trim();
  if (!/^\d{15}$/.test(imsi) || !/^[0-9A-Fa-f]{32}$/.test(sim.k) || !/^[0-9A-Fa-f]{32}$/.test(sim.opc)) {
    print('ERROR: ' + what + ': IMSI must have 15 digits and K/OPc 32 hex chars — skipped');
    return;
  }
  if (imsi.indexOf('00101') !== 0) {
    print('WARNING: ' + what + ': IMSI ' + imsi + ' is not on PLMN 00101 (the gNB/AMF PLMN) — the phone may not attach');
  }
  db.subscribers.deleteOne({ imsi: imsi });
  db.subscribers.insertOne({
    schema_version: 1,
    imsi: imsi,
    msisdn: [],
    imeisv: [],
    mme_host: [],
    mme_realm: [],
    purge_flag: [],
    security: { k: sim.k.toUpperCase(), op: null, opc: sim.opc.toUpperCase(), amf: '8000' },
    ambr: {
      downlink: { value: NumberInt(1), unit: NumberInt(3) },
      uplink:   { value: NumberInt(1), unit: NumberInt(3) }
    },
    slice: slices,
    access_restriction_data: 32,
    subscriber_status: 0,
    network_access_mode: 0,
    subscribed_rau_tau_timer: 12,
    __v: 0
  });
  print('Subscriber ' + imsi + ' provisioned (' + what + ').');
}

provision(SIMS.ue1, [slice(1, '111111', 'internet', true), slice(2, '222222', 'slice2', false)], 'UE-A: SST=1 + SST=2');
provision(SIMS.ue2, [slice(2, '222222', 'slice2', true)], 'UE-B: SST=2 only');

// .count() (not .countDocuments(), which needs an aggregate the mongo:4.4 shell
// rejects under --nodb) — keeps the provision container exit code clean.
print('Done. Total subscribers: ' + db.subscribers.count());
