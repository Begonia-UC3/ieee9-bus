{{/*
Image tag: explicit override, else chart appVersion.
*/}}
{{- define "ieee9-zone.imageTag" -}}
{{- default .Chart.AppVersion .Values.imageTag -}}
{{- end }}

{{/*
Image name — same string as the mode (diesel-gen variant normalised).
The diesel service lives in images/dirs named "diesel-gen", while the
mode value is just "diesel" for UX — map here.
*/}}
{{- define "ieee9-zone.imageName" -}}
{{- $mode := .Values.mode -}}
{{- if eq $mode "diesel" -}}diesel-gen{{- else -}}{{ $mode }}{{- end -}}
{{- end }}

{{/*
Workload name — the Deployment / Service name.

- mode=diesel: canonical image name (`diesel-gen`). Diesel is a
  singleton per tenant (one PV generator on bus 2), so keeping the
  name stable means other assets can still reach it via predictable
  in-namespace DNS.
- mode=dso: instance-scoped (Release name minus the `zone-` prefix
  that Cozystack adds). Lets multiple DSOs coexist in the same
  tenant namespace — e.g. `Ieee9Zone/dso-bus8` and
  `Ieee9Zone/dso-bus7` render as Deployments `dso-bus8` and
  `dso-bus7`. DSOs are source-only (they POST to grid-central and
  serve their own UI via Ingress) so losing the fixed `dso` service
  name costs nothing.
*/}}
{{- define "ieee9-zone.workloadName" -}}
{{- if eq .Values.mode "dso" -}}
{{- .Release.Name | trimPrefix "zone-" -}}
{{- else -}}
{{- include "ieee9-zone.imageName" . -}}
{{- end -}}
{{- end }}

{{/*
Readiness probe path per mode.
*/}}
{{- define "ieee9-zone.probePath" -}}
{{- if eq .Values.mode "dso" -}}/api/status{{- else -}}/api/status{{- end -}}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "ieee9-zone.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "ieee9-zone.workloadName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ include "ieee9-zone.workloadName" . }}
app.kubernetes.io/part-of: ieee9-grid
app.kubernetes.io/managed-by: {{ .Release.Service }}
ieee9.cozystack.io/mode: {{ .Values.mode | quote }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "ieee9-zone.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ieee9-zone.workloadName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ include "ieee9-zone.workloadName" . }}
{{- end }}
