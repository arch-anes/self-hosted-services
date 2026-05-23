{{- define "app.enabled" -}}
{{- $scope := index . 0 -}}
{{- $app := index . 1 -}}
{{- dig $app "enabled" $scope.Values.enableAllApplicationsByDefault $scope.Values.applications | ternary "true" "" -}}
{{- end -}}

{{- define "app.require" -}}
{{- $scope := index . 0 -}}
{{- $thisApp := index . 1 -}}
{{- $requiredApp := index . 2 -}}
{{- $requiredAppDisplay := default $requiredApp (index . 3) -}}
{{- if not (include "app.enabled" (list $scope $requiredApp)) -}}
{{- fail (printf "%s requires %s to be enabled. Please enable %s in your values.yaml" $thisApp $requiredAppDisplay $requiredAppDisplay) -}}
{{- end -}}
{{- end -}}

{{- define "metrics.enabled" -}}
{{- include "app.enabled" (list . "prometheus") -}}
{{- end -}}

{{- define "ldap.base_dn" -}}
{{- $d := trimSuffix "." (lower .Values.fqdn) -}}
{{- printf "dc=%s" (join ",dc=" (splitList "." $d)) -}}
{{- end -}}

{{- define "ip.proxy_ranges" -}}
{{- concat .Values.localIpRanges .Values.cloudFlareIpRanges | toYaml }}
{{- end -}}

{{- define "ip.local_proxy_ranges.string" -}}
{{- .Values.localIpRanges | join "," }}
{{- end -}}

{{- define "ip.private_ranges" -}}
{{- concat .Values.localIpRanges .Values.tailscaleIpRanges | toYaml }}
{{- end -}}

{{- define "ha.enabled" -}}
{{- .Values.highAvailability | ternary "true" "" -}}
{{- end -}}

{{- define "ha.replicas" -}}
{{- if .Values.highAvailability -}}
3
{{- else -}}
1
{{- end -}}
{{- end -}}
