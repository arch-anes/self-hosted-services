{{- define "app.enabled" -}}
  {{- $scope := index . 0 -}}
  {{- $app := index . 1 -}}
  {{- $appValues := default (dict) (index $scope.Values.applications $app) -}}
  {{- dig "enabled" $scope.Values.enableAllApplicationsByDefault $appValues | ternary "true" "" -}}
{{- end -}}

{{- define "app.private" -}}
  {{- $scope := index . 0 -}}
  {{- $app := index . 1 -}}
  {{- $appValues := default (dict) (index $scope.Values.applications $app) -}}
  {{- $privateDefault := $scope.Values.makeAllApplicationsPrivateByDefault | default false -}}
  {{- dig "private" $privateDefault $appValues | ternary "true" "" -}}
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

{{- define "ip.public_proxy_ranges" -}}
  {{- $ranges := .Values.cloudFlareIpRanges -}}
  {{- if .Values.behindTcpProxy -}}
    {{- $proxy := required "tcpProxyIp is required when behindTcpProxy is true" .Values.tcpProxyIp -}}
    {{- $ranges = concat $ranges (list (printf "%s/32" $proxy)) -}}
  {{- end -}}
  {{- $ranges | toYaml }}
{{- end -}}

{{- define "ip.private_proxy_ranges" -}}
  {{- concat .Values.localIpv4Ranges .Values.localIpv6Ranges | toYaml }}
{{- end -}}

{{- define "ip.local_proxy_ranges.string" -}}
  {{- concat .Values.localIpv4Ranges .Values.localIpv6Ranges | join "," }}
{{- end -}}

{{- define "ip.private_ranges" -}}
  {{- concat .Values.localIpv4Ranges .Values.localIpv6Ranges | toYaml }}
{{- end -}}

{{- define "ip.private_ipv4_ranges" -}}
  {{- .Values.localIpv4Ranges | toYaml }}
{{- end -}}

{{- define "ip.private_ipv6_ranges" -}}
  {{- .Values.localIpv6Ranges | toYaml }}
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

{{- define "app.resources.cpu" -}}
  {{- $scope := index . 0 -}}
  {{- $cpu := index . 1 -}}
  {{- if $scope.Values.lowResourceMode -}}
10m
  {{- else -}}
    {{- $cpu -}}
  {{- end -}}
{{- end -}}

{{- define "app.resources.memory" -}}
  {{- $scope := index . 0 -}}
  {{- $memory := index . 1 -}}
  {{- if $scope.Values.lowResourceMode -}}
50Mi
  {{- else -}}
    {{- $memory -}}
  {{- end -}}
{{- end -}}
