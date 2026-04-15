{{/*
Validate required inputs. Fails at render time if user hasn't set modelRepoOwner.
*/}}
{{- define "ieee9ZoneAuto.validate" -}}
{{- if not .Values.modelRepoOwner -}}
{{- fail "modelRepoOwner is required — set it to your GitHub username." -}}
{{- end -}}
{{- end }}

{{/*
Allocate the lowest free slot N from the static pool {2,3,4} by scanning
cluster Namespaces for the label `ieee9-zone-auto/index`. Stability across
re-renders: once this release has created its namespace, the label claim
means lookup re-selects the same N.

Fails fast if all slots are taken (the IEEE-9 topology has only three free
outer PQ buses, so there's no auto-allocated fourth).

We also skip slots whose namespace is labeled but belongs to a different
release, so two concurrent click-deploys don't collide.
*/}}
{{- define "ieee9ZoneAuto.instanceNumber" -}}
{{- $pool := list 2 3 4 -}}
{{- $taken := list -}}
{{- $self := printf "%s/%s" .Release.Namespace .Release.Name -}}
{{- $nsList := (lookup "v1" "Namespace" "" "") -}}
{{- if $nsList -}}
  {{- range $ns := $nsList.items -}}
    {{- $labels := $ns.metadata.labels | default (dict) -}}
    {{- $idxStr := index $labels "ieee9-zone-auto/index" -}}
    {{- $owner  := index $labels "ieee9-zone-auto/owner" -}}
    {{- if and $idxStr (ne $owner $self) -}}
      {{- $taken = append $taken ($idxStr | atoi) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- $chosen := 0 -}}
{{- range $candidate := $pool -}}
  {{- if and (eq $chosen 0) (not (has $candidate $taken)) -}}
    {{- $chosen = $candidate -}}
  {{- end -}}
{{- end -}}
{{- if eq $chosen 0 -}}
{{- fail "No free auto-DSO slot — all of dso-2/dso-3/dso-4 are in use. Delete one first." -}}
{{- end -}}
{{- $chosen -}}
{{- end }}

{{/*
Outer bus for the allocated slot. Mirrors grid-central/main.py ASSET_BUS_MAP.
*/}}
{{- define "ieee9ZoneAuto.outerBus" -}}
{{- $n := include "ieee9ZoneAuto.instanceNumber" . -}}
{{- index .Values._busForSlot $n -}}
{{- end }}

{{/*
Asset id reported upstream: "dso-N".
*/}}
{{- define "ieee9ZoneAuto.assetId" -}}
{{- $n := include "ieee9ZoneAuto.instanceNumber" . -}}
{{- printf "dso-%s" $n -}}
{{- end }}

{{/*
Target namespace for the deployed DSO: tenant-dso-N.
*/}}
{{- define "ieee9ZoneAuto.namespace" -}}
{{- $n := include "ieee9ZoneAuto.instanceNumber" . -}}
{{- printf "tenant-dso-%s" $n -}}
{{- end }}

{{/*
Model repo name: dso-N-model.
*/}}
{{- define "ieee9ZoneAuto.repoName" -}}
{{- $n := include "ieee9ZoneAuto.instanceNumber" . -}}
{{- printf "dso-%s-model" $n -}}
{{- end }}

{{/*
Image tag: explicit override, else chart appVersion.
*/}}
{{- define "ieee9ZoneAuto.imageTag" -}}
{{- default .Chart.AppVersion .Values.imageTag -}}
{{- end }}

{{/*
Ingress host, with the slot number substituted into ingressHostTemplate.
*/}}
{{- define "ieee9ZoneAuto.ingressHost" -}}
{{- $n := include "ieee9ZoneAuto.instanceNumber" . | int -}}
{{- printf .Values.ingressHostTemplate $n -}}
{{- end }}

{{/*
Release-identifying owner label: "<ns>/<name>". Used so allocation is stable
across re-renders for the same release.
*/}}
{{- define "ieee9ZoneAuto.owner" -}}
{{- printf "%s/%s" .Release.Namespace .Release.Name -}}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "ieee9ZoneAuto.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: dso
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: dso
app.kubernetes.io/part-of: ieee9-grid
app.kubernetes.io/managed-by: {{ .Release.Service }}
ieee9-zone-auto/index: {{ include "ieee9ZoneAuto.instanceNumber" . | quote }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "ieee9ZoneAuto.selectorLabels" -}}
app.kubernetes.io/name: dso
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: dso
{{- end }}
