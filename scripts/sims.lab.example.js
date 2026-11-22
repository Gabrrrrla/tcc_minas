// Template for the lab SIM cards. Copy it to scripts/sims.lab.js (NOT versioned) and
// fill in the data of the SIM cards:
//   cp scripts/sims.lab.example.js scripts/sims.lab.js && nano scripts/sims.lab.js
// IMSI: 15 digits, starting with 00101 (gNB PLMN). K and OPc: 32 hex.
// ue1 = UE-A phone (slice 1, DNN internet; also subscribed to slice 2)
// ue2 = UE-B phone (slice 2 only, DNN slice2)
SIMS = {
  ue1: { imsi: '00101XXXXXXXXXX', k: '<32 hex>', opc: '<32 hex>' },
  ue2: { imsi: '00101XXXXXXXXXX', k: '<32 hex>', opc: '<32 hex>' }
};
