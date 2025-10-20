{{- define "metrics.enabled" -}}
{{ and (not .Values.disableAllApplications) .Values.applications.prometheus.enabled }}
{{- end -}}
