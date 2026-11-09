# Download and Review the Published Experiment Data

[Platform quick start](../README.md#quick-start) · [Visitor/operator access](visitor_mode.md) · [Result publication](public_deployment.md)

## Dataset release

Download [**Simulation Dataset v1.0.0 (`data-v1.0.0`)**](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/tag/data-v1.0.0). This release contains **46 `.7z` run archives** and one [**RUN_INDEX.csv**](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/RUN_INDEX.csv) attachment. The data archives cover 25 FD runs, 20 economic-mechanism runs, and one 200-round sequential-shock run. Their recorded horizons total 1,500 rounds; 34 runs are LLM-driven and 12 use rule-based controllers.

The complete set of uploaded attachments totals approximately **44.63 MiB**. The published file manifests declare approximately **6.69 GiB** of uncompressed payload; allow at least **10 GiB** for extraction and filesystem overhead. You can download and load any subset.

**The uploaded data format is `.7z`.** GitHub's automatically generated source ZIP/tarball contains the code, while the named `.7z` attachments contain the experiment records. Use the file links below or the release's **Assets** list.

## Install prerequisites

Install the platform using the [README instructions](../README.md#1-download-and-install). Data viewing requires the Python service and frontend dependencies but no model account.

Install [7-Zip](https://www.7-zip.org/download.html) to open the release archives. On Debian/Ubuntu distributions providing the `7zip` package:

```bash
sudo apt-get update
sudo apt-get install 7zip
7zz -h
```

The examples below use `7zz`. If your installation provides `7z` instead, substitute that command. You can also extract using the 7-Zip graphical application, keeping the directory layout below. Installation and startup examples use Bash and Unix-style paths.

## Download and extract data

Run commands from the platform repository root. The following example downloads FD4 AG and the run index:

```bash
mkdir -p agent/public_results/downloads agent/public_results/runs

curl -fL --retry 3 \
  'https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z' \
  -o agent/public_results/downloads/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z

curl -fL --retry 3 \
  'https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/RUN_INDEX.csv' \
  -o agent/public_results/downloads/RUN_INDEX.csv
```

Check downloaded files against the [SHA-256 list](datasets/data-v1.0.0.sha256), generated from the release's recorded GitHub digests:

```bash
(
  cd agent/public_results/downloads
  sha256sum --ignore-missing -c ../../../docs/datasets/data-v1.0.0.sha256
)
```

`--ignore-missing` skips assets you did not download. Each file that is present must report `OK`. This command uses GNU `sha256sum`; other checksum tools can use the same expected digest values. For a compressed-stream integrity check, run:

```bash
7zz t agent/public_results/downloads/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z
```

List the archive first to see its top-level directory, then extract it:

```bash
7zz l agent/public_results/downloads/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z
7zz x -aos agent/public_results/downloads/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z \
  -oagent/public_results/runs
```

The extraction command assumes the archive contains its named run directory. The required final location is:

```text
agent/public_results/
├── downloads/
│   ├── FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z
│   └── RUN_INDEX.csv
└── runs/
    └── FD4_Staffing-Recovery__AG__run_7c72e4af2bfe/
        ├── run_meta.json
        ├── public_manifest.json
        ├── enterprises/
        ├── public/
        └── projections/
```

If a package contains files directly, extract them into a directory named after that run. If it contains an outer `runs/` wrapper, extract the wrapper into `agent/public_results/`. In every case, `run_meta.json` must be directly below `agent/public_results/runs/<run-directory>/`.

To extract all downloaded packages with a named run directory at their top level:

```bash
for archive in agent/public_results/downloads/*.7z; do
  [ -f "$archive" ] || continue
  7zz x -aos "$archive" -oagent/public_results/runs
done
```

`-aos` skips existing files. Use a fresh publication root when changing dataset versions rather than combining different versions of the same run. Downloaded archives already contain public manifests and review projections; `publish_results.py` and the bundled-example importer are not required to load them. The viewer indexes extracted directories, not compressed files.

## Start the viewer

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor PYTHON_BIN=.venv/bin/python \
  bash scripts/start_integrated_app.sh
```

Open **http://127.0.0.1:3000**, choose **Refresh Run List**, select a run, and open **Review Simulation Results**. The updated viewer recognizes descriptive prefixes and uses the existing star markers, key-experiment cards, and archive highlighting. Visitor mode skips model settings in `scripts/.env` and keeps execution controls locked.

For a data root outside the repository, create `<data-root>/runs/<run-directory>/` and export `SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT` to the parent of `runs/` before startup. The [single-service deployment](public_deployment.md#production-viewer) instead serves the built viewer and API together on port 8000.

## Conditions and coverage

- **AG** is the full organizational agent; FD metadata can label it Full/MA.
- **AG-OC** is the economic operating-objective regime; source C1/C2/C3 labels correspond to RB/AG/AG-OC.
- **RB** is the rule-based reference.
- **NA** removes model-based Analyst diagnosis; **NC** removes additional shared coordination.
- **SA** consolidates each firm's decisions in one agent task. Economic SA uses the corresponding AG-OC regime.

Peer visibility and cobweb calibration variants remain in filenames and the run index. EM-HD's AG visible/hidden pair is the main visibility comparison; the two RB herding references use distinct production rules.

All 46 archives record completed runs. One original observer file is absent in Commons RB (`run_38d277960f2f`): Miner_D at R34, `public/observer_state/day34/end_of_day/Miner_D.json`. Department snapshots remain available. That archive records `display_ready=false`; the other 45 record display readiness.

## Download inventory

The links below are pinned to `data-v1.0.0`. Sizes and checksums come from the GitHub Release API; the checksum file contains all 47 uploaded assets, including the CSV index. Original `run_id` values are retained inside the archives.

### FD1: Order Selection and Market Development

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | — | [FD1_Market-Development__AG__run_55addd212e89.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD1_Market-Development__AG__run_55addd212e89.7z) | 0.17 MiB |
| NA | — | [FD1_Market-Development__NA__run_e4eeccd3505c.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD1_Market-Development__NA__run_e4eeccd3505c.7z) | 0.16 MiB |
| NC | — | [FD1_Market-Development__NC__run_687b949702a4.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD1_Market-Development__NC__run_687b949702a4.7z) | 0.16 MiB |
| RB | — | [FD1_Market-Development__RB__run_ffa1be8e7e88.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD1_Market-Development__RB__run_ffa1be8e7e88.7z) | 0.11 MiB |
| SA | — | [FD1_Market-Development__SA__run_58dbdb031b5a.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD1_Market-Development__SA__run_58dbdb031b5a.7z) | 0.15 MiB |

### FD2: Materials and Procurement

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | — | [FD2_Procurement-Arrival__AG__run_e3e1fe0d07a0.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD2_Procurement-Arrival__AG__run_e3e1fe0d07a0.7z) | 0.17 MiB |
| NA | — | [FD2_Procurement-Arrival__NA__run_17c499f18e2f.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD2_Procurement-Arrival__NA__run_17c499f18e2f.7z) | 0.16 MiB |
| NC | — | [FD2_Procurement-Arrival__NC__run_3cdc77035159.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD2_Procurement-Arrival__NC__run_3cdc77035159.7z) | 0.17 MiB |
| RB | — | [FD2_Procurement-Arrival__RB__run_d3cffad92537.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD2_Procurement-Arrival__RB__run_d3cffad92537.7z) | 0.12 MiB |
| SA | — | [FD2_Procurement-Arrival__SA__run_bf4e85322609.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD2_Procurement-Arrival__SA__run_bf4e85322609.7z) | 0.16 MiB |

### FD3: Capacity Investment

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | — | [FD3_Capacity-Investment__AG__run_2ef12593170e.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD3_Capacity-Investment__AG__run_2ef12593170e.7z) | 0.16 MiB |
| NA | — | [FD3_Capacity-Investment__NA__run_64d154c5eec2.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD3_Capacity-Investment__NA__run_64d154c5eec2.7z) | 0.14 MiB |
| NC | — | [FD3_Capacity-Investment__NC__run_4a7232d8573e.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD3_Capacity-Investment__NC__run_4a7232d8573e.7z) | 0.16 MiB |
| RB | — | [FD3_Capacity-Investment__RB__run_bb8c0a99f597.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD3_Capacity-Investment__RB__run_bb8c0a99f597.7z) | 0.11 MiB |
| SA | — | [FD3_Capacity-Investment__SA__run_a85328e45210.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD3_Capacity-Investment__SA__run_a85328e45210.7z) | 0.15 MiB |

### FD4: Staffing Recovery

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | — | [FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z) | 0.19 MiB |
| NA | — | [FD4_Staffing-Recovery__NA__run_e6e3985c6904.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__NA__run_e6e3985c6904.7z) | 0.17 MiB |
| NC | — | [FD4_Staffing-Recovery__NC__run_a63dba48bb9e.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__NC__run_a63dba48bb9e.7z) | 0.19 MiB |
| RB | — | [FD4_Staffing-Recovery__RB__run_9a97da677b84.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__RB__run_9a97da677b84.7z) | 0.13 MiB |
| SA | — | [FD4_Staffing-Recovery__SA__run_81a725a8a229.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__SA__run_81a725a8a229.7z) | 0.18 MiB |

### FD5: Working Capital

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | — | [FD5_Cash-Sensitive-Operations__AG__run_b5443201a3fa.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD5_Cash-Sensitive-Operations__AG__run_b5443201a3fa.7z) | 0.19 MiB |
| NA | — | [FD5_Cash-Sensitive-Operations__NA__run_c3b7331f7ea7.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD5_Cash-Sensitive-Operations__NA__run_c3b7331f7ea7.7z) | 0.18 MiB |
| NC | — | [FD5_Cash-Sensitive-Operations__NC__run_48e08e6c61e4.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD5_Cash-Sensitive-Operations__NC__run_48e08e6c61e4.7z) | 0.19 MiB |
| RB | — | [FD5_Cash-Sensitive-Operations__RB__run_0e8777469eec.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD5_Cash-Sensitive-Operations__RB__run_0e8777469eec.7z) | 0.13 MiB |
| SA | — | [FD5_Cash-Sensitive-Operations__SA__run_436ed12b0bfe.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD5_Cash-Sensitive-Operations__SA__run_436ed12b0bfe.7z) | 0.18 MiB |

### EM-BW: Bullwhip

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG-OC | — | [EM-BW_Bullwhip__AG-OC__run_5ce2a8bfd692.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-BW_Bullwhip__AG-OC__run_5ce2a8bfd692.7z) | 2.74 MiB |
| AG | — | [EM-BW_Bullwhip__AG__run_81c0584b3204.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-BW_Bullwhip__AG__run_81c0584b3204.7z) | 2.84 MiB |
| RB | — | [EM-BW_Bullwhip__RB__run_6a2d91650ffd.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-BW_Bullwhip__RB__run_6a2d91650ffd.7z) | 0.45 MiB |
| SA | — | [EM-BW_Bullwhip__SA__run_d2eb3056fd85.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-BW_Bullwhip__SA__run_d2eb3056fd85.7z) | 1.35 MiB |

### EM-HD: Herding

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG-OC | Peer-Hidden | [EM-HD_Herding__AG-OC__Peer-Hidden__run_ea0b3e99448f.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__AG-OC__Peer-Hidden__run_ea0b3e99448f.7z) | 1.62 MiB |
| AG-OC | Peer-Visible | [EM-HD_Herding__AG-OC__Peer-Visible__run_3de36a732d15.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__AG-OC__Peer-Visible__run_3de36a732d15.7z) | 1.96 MiB |
| AG | Peer-Hidden | [EM-HD_Herding__AG__Peer-Hidden__run_45d37b82bbb3.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__AG__Peer-Hidden__run_45d37b82bbb3.7z) | 1.52 MiB |
| AG | Peer-Visible | [EM-HD_Herding__AG__Peer-Visible__run_ff2b79856839.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__AG__Peer-Visible__run_ff2b79856839.7z) | 1.73 MiB |
| RB | Peer-Hidden; Heterogeneous-Production | [EM-HD_Herding__RB__Peer-Hidden__Heterogeneous-Production__run_b819cde976b5.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__RB__Peer-Hidden__Heterogeneous-Production__run_b819cde976b5.7z) | 0.70 MiB |
| RB | Peer-Visible; Synchronized-Expansion | [EM-HD_Herding__RB__Peer-Visible__Synchronized-Expansion__run_c6f7d9863ac4.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__RB__Peer-Visible__Synchronized-Expansion__run_c6f7d9863ac4.7z) | 0.61 MiB |
| SA | Peer-Hidden | [EM-HD_Herding__SA__Peer-Hidden__run_13d0b72ea16c.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__SA__Peer-Hidden__run_13d0b72ea16c.7z) | 0.96 MiB |
| SA | Peer-Visible | [EM-HD_Herding__SA__Peer-Visible__run_22f6c806112a.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-HD_Herding__SA__Peer-Visible__run_22f6c806112a.7z) | 1.00 MiB |

### EM-CW: Cobweb

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG-OC | — | [EM-CW_Cobweb__AG-OC__run_e006835dd989.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CW_Cobweb__AG-OC__run_e006835dd989.7z) | 0.48 MiB |
| AG | — | [EM-CW_Cobweb__AG__run_e86464911b6a.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CW_Cobweb__AG__run_e86464911b6a.7z) | 0.67 MiB |
| RB | Bounded-Divergent | [EM-CW_Cobweb__RB__Bounded-Divergent__run_33134dfcdcf0.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CW_Cobweb__RB__Bounded-Divergent__run_33134dfcdcf0.7z) | 0.20 MiB |
| RB | Convergent | [EM-CW_Cobweb__RB__Convergent__run_8a00778a7277.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CW_Cobweb__RB__Convergent__run_8a00778a7277.7z) | 0.21 MiB |
| RB | Near-Neutral | [EM-CW_Cobweb__RB__Near-Neutral__run_7f528f0bd2a7.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CW_Cobweb__RB__Near-Neutral__run_7f528f0bd2a7.7z) | 0.20 MiB |

### EM-CM: Commons

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG-OC | — | [EM-CM_Commons__AG-OC__run_7271174e09fa.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CM_Commons__AG-OC__run_7271174e09fa.7z) | 2.63 MiB |
| AG | — | [EM-CM_Commons__AG__run_16a5f008fc24.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CM_Commons__AG__run_16a5f008fc24.7z) | 3.02 MiB |
| RB | — | [EM-CM_Commons__RB__run_38d277960f2f.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/EM-CM_Commons__RB__run_38d277960f2f.7z) | 0.57 MiB |

### LH-SS: Sequential Shocks

| Condition | Variant | Archive | Size |
| --- | --- | --- | ---: |
| AG | 200-Rounds | [LH-SS_Sequential-Shocks__AG__200-Rounds__run_f3ca897020cb.7z](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/LH-SS_Sequential-Shocks__AG__200-Rounds__run_f3ca897020cb.7z) | 15.16 MiB |

## Troubleshooting

| Situation | Action |
| --- | --- |
| `unzip` cannot open the download | The data attachments use `.7z`; use 7-Zip |
| No results appear | Check the final `runs/<run-directory>/run_meta.json` layout and publication root, then refresh the list |
| Repeated `runs/runs/` or wrapper folders | Move named run directories to the required layout |
| Checksum mismatch or archive test fails | Download that attachment again before extracting |
| New prefixes have no star markers | Update and rebuild the frontend, then reload the page |
| A value is unavailable | Inspect that field's source coverage; a completed run does not imply every archived field exists |

For metric meanings, export options, and the evidence pages, see [Reviewing results](interface_and_artifacts.md). To run new experiments, follow [Agent Setup](../README.md#agent-setup) rather than supplying a model key to the visitor interface.
