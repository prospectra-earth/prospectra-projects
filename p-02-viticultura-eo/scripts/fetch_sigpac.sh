#!/usr/bin/env bash
#
# fetch_sigpac.sh — download the SIGPAC municipality zips listed by NB01's manifest.
#
# NB01 Phase 1d writes one URL per line to:
#     /Volumes/geospatial/ribera_duero/raw/sigpac/sigpac_urls_<year>.txt
#
# This reads those lists, downloads what is missing, and uploads into the matching year
# folder in the Volume. Already-present files are skipped, so it is safe to re-run after
# an interruption.
#
# Usage
#     ./fetch_sigpac.sh                       # 2022-2025
#     ./fetch_sigpac.sh 2024                  # one year
#
# Env
#     DATABRICKS_PROFILE   CLI profile           (default: DEFAULT)
#     SIGPAC_VOLUME        Volume sigpac folder  (default: /Volumes/geospatial/ribera_duero/raw/sigpac)
#     PARALLEL             concurrent downloads  (default: 6)
#
# Requires an authenticated Databricks CLI — the Volume is not mounted locally.
#
# Licence: ITACyL permits free use of SIGPAC but PROHIBITS commercial exploitation.
# Downloading is fine; selling a product built on it is not, on the current terms.

set -euo pipefail

PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
VOLUME="${SIGPAC_VOLUME:-/Volumes/geospatial/ribera_duero/raw/sigpac}"
PARALLEL="${PARALLEL:-6}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# NB01 loads ONE snapshot — reference geometry is near-static and is refreshed manually.
# Pass other years explicitly only if you are deliberately comparing vintages.
YEARS=("$@")
[[ ${#YEARS[@]} -eq 0 ]] && YEARS=(2025)

command -v databricks >/dev/null || { echo "databricks CLI not found"; exit 1; }

echo "profile $PROFILE | volume $VOLUME | parallel $PARALLEL | years ${YEARS[*]}"
echo

for YEAR in "${YEARS[@]}"; do
  echo "=== $YEAR ==="

  LIST="${WORKDIR}/urls_${YEAR}.txt"
  if ! databricks fs cat "dbfs:${VOLUME}/sigpac_urls_${YEAR}.txt" -p "$PROFILE" > "$LIST" 2>/dev/null; then
    echo "  no manifest for $YEAR — run NB01 Phase 1d first"
    continue
  fi
  sed -i.bak '/^[[:space:]]*$/d' "$LIST" && rm -f "${LIST}.bak"
  echo "  $(wc -l < "$LIST" | tr -d ' ') files in manifest"

  # One listing of what is already uploaded, rather than a check per file.
  HAVE="${WORKDIR}/have_${YEAR}.txt"
  databricks fs ls "dbfs:${VOLUME}/${YEAR}" -p "$PROFILE" 2>/dev/null \
    | awk '{print $NF}' | grep '\.zip$' | sed 's|.*/||' > "$HAVE" || : > "$HAVE"
  echo "  $(wc -l < "$HAVE" | tr -d ' ') already in the Volume"

  # Build the to-do list.
  TODO="${WORKDIR}/todo_${YEAR}.txt"
  : > "$TODO"
  while read -r URL; do
    grep -qxF "$(basename "$URL")" "$HAVE" || echo "$URL" >> "$TODO"
  done < "$LIST"

  N=$(wc -l < "$TODO" | tr -d ' ')
  if [[ "$N" -eq 0 ]]; then
    echo "  nothing to fetch"
    continue
  fi

  STAGE="${WORKDIR}/${YEAR}"
  mkdir -p "$STAGE"
  echo "  downloading $N ..."

  # One curl per URL, PARALLEL at a time. -f so HTTP errors are not written as files.
  xargs -P "$PARALLEL" -I{} sh -c \
    'curl -sS -f --retry 3 --retry-delay 2 -o "'"$STAGE"'/$(basename "$1")" "$1" || echo "    FAILED $(basename "$1")"' \
    _ {} < "$TODO"

  GOT=$(find "$STAGE" -name '*.zip' -size +0 | wc -l | tr -d ' ')
  echo "  downloaded $GOT of $N"
  [[ "$GOT" -eq 0 ]] && continue

  echo "  uploading to ${VOLUME}/${YEAR}/ ..."
  for F in "$STAGE"/*.zip; do
    databricks fs cp "$F" "dbfs:${VOLUME}/${YEAR}/$(basename "$F")" -p "$PROFILE" --overwrite \
      || echo "    upload FAILED $(basename "$F")"
  done
  echo "  $YEAR done"
  echo
done

echo "Re-run NB01 from the Phase 2 gate."
