{{- define "tunnel.image.repository" -}}ghcr.io/heiher/hev-socks5-tunnel{{- end -}}
{{- define "tunnel.image.tag" -}}2.13{{- end -}}
{{- define "tunnel.image.ref" -}}{{ printf "%s:%s" (include "tunnel.image.repository" .) (include "tunnel.image.tag" .) }}{{- end -}}
{{- define "tunnel.image.pullPolicy" -}}IfNotPresent{{- end -}}

{{- define "tunnel.env.map" -}}
SOCKS5_ADDR: gluetun.{{ .Values.applicationsNamespace }}.svc.cluster.local
SOCKS5_UDP_MODE: tcp
IPV4_INCLUDED_ROUTES: 0.0.0.0/0
IPV4_EXCLUDED_ROUTES: |-
  10.42.0.0/16
  10.43.0.0/16
{{- end -}}

{{- define "tunnel.env.list" -}}
{{- $m := include "tunnel.env.map" . | fromYaml -}}
{{- $keys := keys $m | sortAlpha -}}
{{- $list := list -}}
{{- range $keys -}}
{{- $list = append $list (dict "name" . "value" (get $m .)) -}}
{{- end -}}
{{- toYaml $list | trim -}}
{{- end -}}

{{- define "tunnel.resources" -}}
requests:
  cpu: 250m
  memory: 64Mi
limits:
  memory: 128Mi
{{- end -}}

{{- define "tunnel.securityContext" -}}
privileged: true
runAsUser: 0
runAsGroup: 0
runAsNonRoot: false
capabilities:
  add:
    - NET_ADMIN
  drop: []
{{- end -}}

{{/* Kubernetes Deployment container */}}
{{- define "tunnel.deployment.container" -}}
- name: tunnel
  image: {{ include "tunnel.image.ref" . }}
  imagePullPolicy: {{ include "tunnel.image.pullPolicy" . }}
  securityContext:
    {{- include "tunnel.securityContext" . | nindent 4 }}
  resources:
    {{- include "tunnel.resources" . | nindent 4 }}
  env:
    {{- include "tunnel.env.list" . | nindent 4 }}
{{- end -}}

{{/* TrueCharts-style values */}}
{{- define "tunnel.truecharts.image" -}}
tunnelImage:
  repository: {{ include "tunnel.image.repository" . }}
  pullPolicy: {{ include "tunnel.image.pullPolicy" . }}
  tag: {{ include "tunnel.image.tag" . | quote }}
{{- end -}}

{{- define "tunnel.truecharts.container" -}}
tunnel:
  enabled: true
  probes:
    liveness:
      enabled: false
    readiness:
      enabled: false
    startup:
      enabled: false
  imageSelector: tunnelImage
  securityContext:
    {{- include "tunnel.securityContext" . | nindent 4 }}
  resources:
    {{- include "tunnel.resources" . | nindent 4 }}
  env:
    {{- include "tunnel.env.map" . | nindent 4 }}
{{- end -}}
