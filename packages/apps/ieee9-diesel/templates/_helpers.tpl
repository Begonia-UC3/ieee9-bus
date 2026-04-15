{{/*
Image tag: explicit override, else chart appVersion.
*/}}
{{- define "ieee9-diesel.imageTag" -}}
{{- default .Chart.AppVersion .Values.imageTag -}}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "ieee9-diesel.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: diesel-gen
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: diesel-gen
app.kubernetes.io/part-of: ieee9-grid
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "ieee9-diesel.selectorLabels" -}}
app.kubernetes.io/name: diesel-gen
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: diesel-gen
{{- end }}
