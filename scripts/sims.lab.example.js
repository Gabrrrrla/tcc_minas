// Modelo dos chips do lab. Copie para scripts/sims.lab.js (NÃO versionado) e
// preencha com os dados dos chips azuis:
//   cp scripts/sims.lab.example.js scripts/sims.lab.js && nano scripts/sims.lab.js
// IMSI: 15 dígitos, começando em 00101 (PLMN do gNB). K e OPc: 32 hex.
// ue1 = celular da UE-A (fatia 1, DNN internet; também assinado na fatia 2)
// ue2 = celular da UE-B (só fatia 2, DNN slice2)
SIMS = {
  ue1: { imsi: '00101XXXXXXXXXX', k: '<32 hex>', opc: '<32 hex>' },
  ue2: { imsi: '00101XXXXXXXXXX', k: '<32 hex>', opc: '<32 hex>' }
};
