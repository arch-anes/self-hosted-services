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

{{- define "gpu.device" -}}
{{- $scope := index . 0 -}}
{{- $appName := index . 1 -}}
{{- $gpuVendor := index . 2 -}}
{{- $gpuCount := 1 -}}
{{- if gt (len .) 3 -}}
  {{- $gpuCount = index . 3 -}}
{{- end -}}
{{- if $gpuVendor -}}
  {{- if eq $gpuVendor "intel" -}}
    {{- if not (include "app.enabled" (list $scope "intel_gpu")) -}}
      {{- fail (printf "Intel GPU is selected for %s but intel_gpu is not enabled" $appName) -}}
    {{- end -}}
    {{- printf "gpu.intel.com/i915: %v" $gpuCount -}}
  {{- else if eq $gpuVendor "intel_xe" -}}
    {{- if not (include "app.enabled" (list $scope "intel_gpu")) -}}
      {{- fail (printf "Intel GPU is selected for %s but intel_gpu is not enabled" $appName) -}}
    {{- end -}}
    {{- printf "gpu.intel.com/xe: %v" $gpuCount -}}
  {{- else if eq $gpuVendor "nvidia" -}}
    {{- if not (include "app.enabled" (list $scope "nvidia_gpu")) -}}
      {{- fail (printf "NVIDIA GPU is selected for %s but nvidia_gpu is not enabled" $appName) -}}
    {{- end -}}
    {{- printf "nvidia.com/gpu: %v" $gpuCount -}}
  {{- else if eq $gpuVendor "amd" -}}
    {{- if not (include "app.enabled" (list $scope "amd_gpu")) -}}
      {{- fail (printf "AMD GPU is selected for %s but amd_gpu is not enabled" $appName) -}}
    {{- end -}}
    {{- printf "amd.com/gpu: %v" $gpuCount -}}
  {{- else -}}
    {{- fail (printf "Unknown GPU vendor '%s' selected for %s" $gpuVendor $appName) -}}
  {{- end -}}
{{- end -}}
{{- end -}}
