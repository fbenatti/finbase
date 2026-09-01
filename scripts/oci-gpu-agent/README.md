# OCI GPU Agent — nova fonte de dados de utilização de GPU

## Por que existe

A OCI **não publica** `GpuUtilization`/`GpuMemoryUtilization` para shapes
`VM.GPU.*` (somente para Bare Metal `BM.GPU.*`). Confirmado na prática:
a coleta em `oci_computeagent` retorna 0 pontos e 0 erros para as
`VM.GPU.A10.1` do Wisper.

A solução é um **agente no guest** que lê `nvidia-smi` e publica as métricas no
**OCI Monitoring em um namespace customizado** (`custom_gpu`). A partir daí o
pipeline existente (coletor horário → bronze → gold → dashboard) consome sem
nenhuma mudança de arquitetura.

```
VM.GPU.A10.1 (nvidia-smi) ──► agente oci_gpu_agent.py ──► OCI Monitoring (custom_gpu)
                                                              ▲
oci_operational_monitoring.py (job horário, lê custom_gpu) ───┘
        │
        ▼
gold.dest_oci_operational_metrics ──► WisperDashboard (Utilização GPU: xx% [agente nvidia-smi])
```

## Métricas publicadas (namespace `custom_gpu`)

| Métrica | Unidade |
|---|---|
| `gpu_utilization_percent` | % (nvidia-smi utilization.gpu) |
| `gpu_memory_utilization_percent` | % (memory.used/memory.total) |
| `gpu_temperature_celsius` | °C |
| `gpu_power_watts` | W |
| `gpu_memory_used_bytes` / `gpu_memory_total_bytes` | bytes |

Dimensões: `resourceId` (OCID da instância), `hostname`, `shape`, `gpu_index`.
Um datapoint por GPU por ciclo (default 60s).

## Passo 1 — Permissão (IAM)

1. Criar um **Dynamic Group** que inclua as instâncias GPU. Ex. regra:
   ```
   ALL {instance.compartment.id = 'ocid1.compartment.oc1..<wisper_genai>'}
   ```
   (ou por shape: `ALL {instance.shape.id = 'ocid1.capacityshape.oc1..<VM.GPU.A10.1>'}`)
2. Criar a **policy** no tenancy/root compartment:
   ```
   Allow dynamic group <GPU_AGENT_DG> to use metrics in tenancy where request.operation='PostMetricData'
   ```
   ou, mais simples (se aceitável):
   ```
   Allow dynamic group <GPU_AGENT_DG> to manage metrics in tenancy
   ```

## Passo 2 — Instalar o agente em cada VM.GPU

```bash
sudo bash scripts/oci-gpu-agent/install-agent.sh 60
```

Pré-requisitos na instância: driver NVIDIA (`nvidia-smi` OK) e o SDK OCI
(`pip3 install --user oci`). O agente usa **Instance Principal** (sem chaves).
Para testar sem systemd: `python3 oci_gpu_agent.py --once`.

Se as instâncias não tiverem dynamic group, é possível autenticar por chave de
API: copiar `~/.oci/config` (mesmo layout do CLI) e rodar com
`OCI_AGENT_AUTH=api_key`.

## Passo 3 — Ativar a leitura no coletor

Adicionar o namespace customizado na config da conexão OCI
(`source_connection.config_json`):

```json
{
  "...": "...",
  "custom_metric_namespaces": {
    "custom_gpu": [
      "gpu_utilization_percent",
      "gpu_memory_utilization_percent",
      "gpu_temperature_celsius",
      "gpu_power_watts",
      "gpu_memory_used_bytes",
      "gpu_memory_total_bytes"
    ]
  }
}
```

Atualização direta (dev):
```sql
UPDATE source_connection
SET config_json = config_json ||
  '{"custom_metric_namespaces":{"custom_gpu":["gpu_utilization_percent","gpu_memory_utilization_percent","gpu_temperature_celsius","gpu_power_watts","gpu_memory_used_bytes","gpu_memory_total_bytes"]}}'
WHERE connector_type = 'oci';
```

O coletor já passa a consultar `custom_gpu` **somente** nas instâncias com
`.GPU` no shape (código em `oci_operational_monitoring.py`:
`custom_metric_namespaces()` + iteração de `metrics_config`).

## Passo 4 — Dashboard

O payload v2 (`wisper_dashboard.py`) já resolve `gpu_utilization` de duas fontes:
`oci_computeagent` (BM.GPU) ou `custom_gpu` (agente), e o frontend exibe
"Utilização GPU: 42,1% (agente nvidia-smi)". Nenhuma mudança de UI é necessária.

## Nota sobre Dedicated AI Clusters

Os **Dedicated AI Clusters** (`whisper-large-v3-turbo-a10` 20×A10_X1 e
`llama-scout-prod` 12×LARGE_GENERIC_V2) são gerenciados pela OCI e **não
expõem** utilização de GPU via Monitoring. Para eles, o indicador executivo
recomendado é **eficiência faturada**: tokens ÷ (AI unit-h / GPU-h faturadas) —
já calculável com o payload v2 (`unit_economics`).
