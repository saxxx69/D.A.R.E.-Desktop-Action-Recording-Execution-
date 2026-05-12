# D.A.R.E. Recordings Organization

## Directory Structure

```
recordings/
├── web-pages/
│   ├── github.com/
│   │   ├── runs/
│   │   │   └── run-abc-123/
│   │   │       ├── raw/
│   │   │       ├── processed/
│   │   │       ├── dsl/
│   │   │       ├── intents/
│   │   │       ├── validation/
│   │   │       └── metadata.json
│   │   ├── README.md
│   │   └── config.json
│   │
│   ├── gmail.com/
│   │   ├── runs/
│   │   └── ...
│   │
│   └── [site-name]/
│       └── ...
│
└── apps/
    ├── vs-code/
    │   ├── runs/
    │   │   └── run-xyz-789/
    │   │       ├── raw/
    │   │       ├── processed/
    │   │       └── ...
    │   ├── README.md
    │   └── config.json
    │
    ├── photoshop/
    │   ├── runs/
    │   └── ...
    │
    └── [app-name]/
        └── ...
```

## Workflow Rules

### 1. **Creazione di una nuova registrazione**

**Regola:** Ogni registrazione deve stare nella sua cartella di destinazione.

```bash
# Per una pagina web
./recordings/web-pages/[site-name]/

# Per un'app
./recordings/apps/[app-name]/
```

### 2. **Procedura Standard**

1. **Identifica il target** — È una pagina web o un'app?
   - Se web: vai in `recordings/web-pages/[site-name]/`
   - Se app: vai in `recordings/apps/[app-name]/`

2. **Crea la cartella se non esiste**
   ```bash
   mkdir -p recordings/web-pages/github.com
   # oppure
   mkdir -p recordings/apps/vs-code
   ```

3. **Avvia la registrazione con D.A.R.E.**
   ```bash
   cd recordings/web-pages/github.com
   dare record --notes "login to github"
   # Crea automaticamente: runs/<run-id>/
   ```

4. **Tutti i file rimangono in quella cartella**
   - Raw events: `runs/<run-id>/raw/raw_events.jsonl`
   - UI state: `runs/<run-id>/processed/ui_state.json`
   - DSL: `runs/<run-id>/dsl/action.dsl.yaml`
   - Intents: `runs/<run-id>/intents/`
   - Validation: `runs/<run-id>/validation/`

### 3. **Metadata per Ogni Target**

Crea un `README.md` in ogni cartella:

```markdown
# GitHub.com Automations

## Purpose
Record and execute GitHub workflows: login, navigate repos, create issues

## Recordings
- run-abc-123: Login workflow
- run-xyz-789: Create new repo

## Configuration
See config.json for site-specific settings
```

Crea un `config.json` per configurazioni specifiche:

```json
{
  "target_url": "https://github.com",
  "target_type": "web-page",
  "platform": "browser-chrome",
  "authentication": "oauth",
  "notes": "GitHub automation suite"
}
```

### 4. **Convenzioni di Naming**

**Per cartelle sito web:**
- `github.com` (non `Github`, non `GitHub Website`)
- `gmail.com` (non `Gmail`)
- `example.com` (dominio esatto)

**Per cartelle app:**
- `vs-code` (nome-versione se necessario)
- `photoshop` (nome app ufficiale, kebab-case)
- `slack-desktop` (con versione se multiple)

**Per run ID:**
- D.A.R.E. lo genera automaticamente: `run-<timestamp>`
- Non modificare mai il nome del run

### 5. **Checklist Prima di Registrare**

- [ ] Cartella target esiste? (`recordings/web-pages/[site]` o `recordings/apps/[app]`)
- [ ] README.md presente nella cartella?
- [ ] config.json presente nella cartella?
- [ ] Sei nella cartella giusta prima di avviare `dare record`?
- [ ] Sai cosa registrare? (uno scenario coerente)

### 6. **Isolamento dei Dati**

**Importante:** Ogni registrazione rimane nella sua cartella.

```
recordings/
├── web-pages/
│   ├── github.com/
│   │   └── runs/  ← Tutte le registrazioni GitHub qui
│   └── gmail.com/
│       └── runs/  ← Tutte le registrazioni Gmail qui
└── apps/
    └── vs-code/
        └── runs/  ← Tutte le registrazioni VS Code qui
```

Non mescolare: GitHub data rimane in `github.com/`, Gmail data rimane in `gmail.com/`, ecc.

## Example Workflow

```bash
# 1. Decide: voglio registrare il login a GitHub
cd recordings/web-pages/github.com

# 2. Avvia registrazione (crea automaticamente runs/<run-id>/)
dare record --notes "Login workflow"

# 3. Interagisci col browser (username, password, click)
# ... [user performs actions] ...
# Premi doppio-ESC o attendi 30 secondi di inattività

# 4. Risultati salvati automaticamente:
# recordings/web-pages/github.com/runs/run-2026-05-06-123456/

# 5. Normalizza, genera DSL, valida, esegui — tutto nella stessa cartella
cd runs/run-2026-05-06-123456
dare normalize
dare generate-dsl
dare validate
dare execute
```

## Maintenance

**Per visualizzare tutte le registrazioni:**
```bash
find recordings -name "metadata.json" | xargs cat
```

**Per ripulire recording non usate:**
```bash
# Metti le cartelle vuote in un archivio
tar czf recordings-archive.tar.gz recordings/
```

---

**Mantenere ordine = mantenere traccia di ciò che funziona e perché.**

