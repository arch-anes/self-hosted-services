{{- define "metrics.enabled" -}}
{{ and (not .Values.disableAllApplications) .Values.applications.prometheus.enabled }}
{{- end -}}

{{- define "ldap.base_dn" -}}
{{- $d := trimSuffix "." (lower .Values.fqdn) -}}
{{- printf "dc=%s" (join ",dc=" (splitList "." $d)) -}}
{{- end -}}

{{- define "ip.proxy_ranges" -}}
{{- concat .Values.localIpRanges .Values.cloudFlareIpRanges | toYaml }}
{{- end -}}

{{- define "ip.mail_ranges" -}}
{{- concat .Values.localIpRanges .Values.sesRanges | toYaml }}
{{- end -}}

{{- define "ip.mail_ranges.map" -}}
{{- $ranges := concat .Values.localIpRanges .Values.sesRanges -}}
{{- range $ranges -}}
{{ . }}: ""
{{ end -}}
{{- end -}}

{{- define "ip.private_ranges" -}}
{{- concat .Values.localIpRanges .Values.tailscaleIpRanges | toYaml }}
{{- end -}}
