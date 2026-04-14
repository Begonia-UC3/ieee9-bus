{{/*
Image tag: explicit override, else chart appVersion.
*/}}
{{- define "ieee9-grid.imageTag" -}}
{{- default .Chart.AppVersion .Values.imageTag -}}
{{- end }}

{{/*
Common labels. Call as: include "ieee9-grid.labels" (dict "ctx" . "component" "grid-central")
*/}}
{{- define "ieee9-grid.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: ieee9-grid
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
{{- if .ctx.Chart.AppVersion }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "ieee9-grid.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}
