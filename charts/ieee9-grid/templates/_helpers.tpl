{{/*
Expand the name of the chart.
*/}}
{{- define "ieee9-grid.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified release name.
*/}}
{{- define "ieee9-grid.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label.
*/}}
{{- define "ieee9-grid.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels. Pass a dict: {"ctx": ., "component": "grid-central"}
*/}}
{{- define "ieee9-grid.labels" -}}
helm.sh/chart: {{ include "ieee9-grid.chart" .ctx }}
app.kubernetes.io/name: {{ include "ieee9-grid.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- if .ctx.Chart.AppVersion }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/part-of: ieee9-grid
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
{{- end }}

{{/*
Selector labels. Pass a dict: {"ctx": ., "component": "grid-central"}
*/}}
{{- define "ieee9-grid.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ieee9-grid.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "ieee9-grid.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ieee9-grid.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Full image reference for a given service name.
*/}}
{{- define "ieee9-grid.image" -}}
{{- $tag := default .ctx.Chart.AppVersion .ctx.Values.image.tag -}}
{{- printf "%s/%s/%s:%s" .ctx.Values.image.registry .ctx.Values.image.repository .component $tag -}}
{{- end }}
