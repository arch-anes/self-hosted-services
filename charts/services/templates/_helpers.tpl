{{- define "metrics.enabled" -}}
{{ and (not .Values.disableAllApplications) .Values.applications.prometheus.enabled }}
{{- end -}}

{{- define "ldap.base_dn" -}}
{{- $d := trimSuffix "." (lower .Values.fqdn) -}}
{{- printf "dc=%s" (join ",dc=" (splitList "." $d)) -}}
{{- end -}}
