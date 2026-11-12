# Contexto do laboratório 5G (open5GK3S + OCUDU + LiteOn)

Base de conhecimento sobre o projeto, as máquinas, as decisões e os planos. Serve para retomar o trabalho em uma conversa nova
com um assistente de IA (ou com outra pessoa) sem perder nada do que já foi descoberto.

> **Como usar numa conversa nova:** peça para ler este repositório inteiro antes de qualquer coisa
> (comece por este README e pelo `07-regras-e-preferencias.md`). Depois diga o que quer fazer.
>
> **Regra:** este repositório deve ser atualizado em **toda** conversa (ver regra 12 em `07-regras-e-preferencias.md`).

Última atualização: **2026-10-28** (pesquisa: aceleração do PHY do OCUDU por GPU; usuário tem RTX 5000 Ada).

## Resumo em 1 minuto

- **open5GK3S** (https://github.com/Miguelclunardi/open5GK3S, privado): core 5G **Open5GS 2.7.5** em **Kubernetes (k3s)**
  num único nó, instalado por `install.sh` a partir de um único `config.env`, testado com **UERANSIM** (gNB e UE simulados).
- **Hoje roda no servidor `torre2`** (Ubuntu Server 24.04): instalado e validado em 2026-10-20, `./test.sh` 7/7.
  O servidor é o **ambiente principal**: edita-se lá, e o git vai **do servidor para o GitHub**.
- **Próxima fase:** trocar o UERANSIM por RAN real: **OCUDU** (gNB CU/DU) + O-RU **LiteOn**, que rodam em outra máquina,
  o **`innovaserver`**, hoje com tudo em bare metal (inclusive um Open5GS 2.8.0 próprio). O core passa a ser o do torre2,
  ligado ao innovaserver por **fibra** (Intel 82599ES no torre2 ↔ Intel **E810** no innovaserver).
- Situação: **fase de estudo**. Nada da fase RAN foi alterado em nenhuma máquina.

## Setores

- **Setor 1 — core em Kubernetes com UERANSIM** (concluído, congelado): arquivos 01–08 abaixo; open5GK3S tag `v1.0-ueransim`.
- **Setor 2 — core em Kubernetes + OCUDU + LiteOn** (em andamento): pasta [`setor-2-core-ocudu/`](setor-2-core-ocudu/README.md); open5GK3S branch `ocudu`.

## Arquivos (Setor 1 e infraestrutura comum)

| Arquivo | Conteúdo |
|---|---|
| [01-visao-geral.md](01-visao-geral.md) | Objetivo, linha do tempo, onde está cada coisa |
| [02-maquinas.md](02-maquinas.md) | Desktop, torre2 e innovaserver: hardware, acesso, rede, serviços, particularidades |
| [03-projeto-open5gk3s.md](03-projeto-open5gk3s.md) | Como o projeto funciona por dentro (cadeia até o Open5GS, arquivos, scripts, decisões) |
| [04-operacao.md](04-operacao.md) | Fluxo de trabalho, comandos do dia a dia, slices, assinantes, WebUI |
| [05-problemas-resolvidos.md](05-problemas-resolvidos.md) | Todos os problemas encontrados e como foram resolvidos |
| [06-ran-ocudu-liteon.md](06-ran-ocudu-liteon.md) | Levantamento do innovaserver, configuração do gNB, PTP, E810, proposta de arquitetura e pendências |
| [07-regras-e-preferencias.md](07-regras-e-preferencias.md) | Regras do usuário (obrigatório ler) |
| [08-glossario.md](08-glossario.md) | Termos de 5G, Kubernetes e do projeto |
| [09-decisoes.md](09-decisoes.md) | **Decisões técnicas** (saíram do repositório público em 2026-10-28; registrar novas aqui) |
| [10-ocudu-k8s-diario.md](10-ocudu-k8s-diario.md) | Diário do OCUDU no Kubernetes: passo a passo com datas, resultados e problemas |

Documentação detalhada do core (README, arquitetura, decisões numeradas 1–29, troubleshooting) fica no próprio
repositório `open5GK3S`, pasta `docs/`. Este repositório resume e complementa com o que **não** está lá.
