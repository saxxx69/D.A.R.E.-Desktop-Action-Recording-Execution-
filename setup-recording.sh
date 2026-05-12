#!/bin/bash
# D.A.R.E. Recording Setup Script
# Crea automaticamente la struttura per un nuovo sito web o app

set -e

echo "🎬 D.A.R.E. Recording Setup"
echo "============================"
echo ""

# Determina tipo di target
echo "Tipo di target?"
echo "1) Web Page (sito web)"
echo "2) App (applicazione desktop)"
read -p "Scegli (1 o 2): " target_type

if [ "$target_type" = "1" ]; then
    target_category="web-pages"
    echo "Enter domain (e.g., github.com, gmail.com):"
    read -p "Domain: " target_name
    app_or_url="https://${target_name}"
elif [ "$target_type" = "2" ]; then
    target_category="apps"
    echo "Enter app name (e.g., vs-code, photoshop, slack-desktop):"
    read -p "App name: " target_name
    app_or_url="${target_name}"
else
    echo "❌ Scelta non valida"
    exit 1
fi

# Crea cartella
target_dir="recordings/${target_category}/${target_name}"

if [ -d "$target_dir" ]; then
    echo "⚠️  Cartella già esiste: $target_dir"
    read -p "Sovrascrivere? (y/n): " confirm
    if [ "$confirm" != "y" ]; then
        echo "Annullato."
        exit 1
    fi
fi

mkdir -p "$target_dir/runs"

# Crea README.md
cat > "$target_dir/README.md" << READMEEOF
# ${target_name} Automations

## Purpose
Describe what you'll automate here. Example: Login workflows, form filling, file operations, etc.

## Target
- **URL/App**: ${app_or_url}
- **Type**: $([ "$target_type" = "1" ] && echo "Web Page" || echo "Native App")
- **Platform**: $([ "$target_type" = "1" ] && echo "Browser" || echo "macOS/Windows/Linux")

## Recordings

### run-YYYY-MM-DD-scenario
- **Scenario**: What are you automating? (e.g., "Login workflow")
- **Steps**: Step-by-step description
- **Status**: ✓ Complete / ⏳ In Progress / ⚠️ Needs Review
- **Notes**: Any special notes or requirements

## Configuration
See \`config.json\` for target-specific settings.

## Running Automations

\`\`\`bash
cd $target_dir

# List all runs
dare runs list

# Execute a recording (dry-run)
dare execute --run <run-id> --live false

# Execute live
dare execute --run <run-id> --live true
\`\`\`

READMEEOF

# Crea config.json
cat > "$target_dir/config.json" << CONFIGEOF
{
  "target": "${app_or_url}",
  "target_type": "$([ "$target_type" = "1" ] && echo "web-page" || echo "native-app")",
  "platform": "$([ "$target_type" = "1" ] && echo "browser" || echo "desktop")",
  "authentication": "none",
  "requires_env_vars": [],
  "timeout_seconds": 30,
  "notes": "Add your notes here"
}
CONFIGEOF

echo ""
echo "✅ Setup completato!"
echo ""
echo "Struttura creata:"
echo "  $target_dir/"
echo "  ├── runs/"
echo "  ├── README.md"
echo "  └── config.json"
echo ""
echo "Prossimi passi:"
echo "  1. cd $target_dir"
echo "  2. Aggiorna README.md con i dettagli del target"
echo "  3. Aggiorna config.json con le impostazioni"
echo "  4. dare record --notes 'descrizione della registrazione'"
echo ""

