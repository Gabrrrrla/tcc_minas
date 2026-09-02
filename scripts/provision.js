// Provisions test subscribers into Open5GS MongoDB.
// Runs automatically on docker compose up via the provision service.
//
// UE 1 (IMSI 001010000000001): assinado nas duas slices — SST=1 (eMBB/internet) e SST=2 (URLLC/slice2)
// UE 2 (IMSI 001010000000002): assinado apenas na SST=2 (slice de missão crítica, UC1)
//
// Credenciais de TESTE — substituir pelos valores reais dos SIM cards antes de ir ao lab

db = connect('mongodb://mongo/open5gs');

// ---------- UE 1 ----------
db.subscribers.deleteOne({ imsi: '001010000000001' });
db.subscribers.insertOne({
  schema_version: 1,
  imsi: '001010000000001',
  msisdn: [],
  imeisv: [],
  mme_host: [],
  mme_realm: [],
  purge_flag: [],
  security: {
    k: '465B5CE8B199B49FAA5F0A2EE238A6BC',
    op: null,
    opc: 'E8ED289DEBA952E4283B54E88E6183CA',
    amf: '8000'
  },
  ambr: {
    downlink: { value: NumberInt(1), unit: NumberInt(3) },
    uplink:   { value: NumberInt(1), unit: NumberInt(3) }
  },
  slice: [
    {
      sst: 1,
      default_indicator: true,
      session: [
        {
          name: 'internet',
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
        }
      ],
      _id: new ObjectId()
    },
    {
      sst: 2,
      default_indicator: false,
      session: [
        {
          name: 'slice2',
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
        }
      ],
      _id: new ObjectId()
    }
  ],
  access_restriction_data: 32,
  subscriber_status: 0,
  network_access_mode: 0,
  subscribed_rau_tau_timer: 12,
  __v: 0
});
print('Subscriber 001010000000001 provisioned (SST=1 + SST=2).');

// ---------- UE 2 ----------
db.subscribers.deleteOne({ imsi: '001010000000002' });
db.subscribers.insertOne({
  schema_version: 1,
  imsi: '001010000000002',
  msisdn: [],
  imeisv: [],
  mme_host: [],
  mme_realm: [],
  purge_flag: [],
  security: {
    k: '465B5CE8B199B49FAA5F0A2EE238A6BC',
    op: null,
    opc: 'E8ED289DEBA952E4283B54E88E6183CA',
    amf: '8000'
  },
  ambr: {
    downlink: { value: NumberInt(1), unit: NumberInt(3) },
    uplink:   { value: NumberInt(1), unit: NumberInt(3) }
  },
  slice: [
    {
      sst: 2,
      default_indicator: true,
      session: [
        {
          name: 'slice2',
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
        }
      ],
      _id: new ObjectId()
    }
  ],
  access_restriction_data: 32,
  subscriber_status: 0,
  network_access_mode: 0,
  subscribed_rau_tau_timer: 12,
  __v: 0
});
print('Subscriber 001010000000002 provisioned (SST=2 only).');

print('Done. Total subscribers: ' + db.subscribers.countDocuments());
