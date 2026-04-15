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
Workload name — the Deployment / Service name. Equals the image name
so in-namespace DNS works: mode=diesel → svc/diesel-gen, mode=dso →
svc/dso, etc. This also means two Ieee9Zone CRs with the same mode in
the same namespace would collide — by design, one asset instance per
outer bus.
*/}}
{{- define "ieee9-zone.workloadName" -}}
{{- include "ieee9-zone.imageName" . -}}
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
