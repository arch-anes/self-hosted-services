{{- define "tunnel.sidecar" -}}
- name: tunnel
  image: ghcr.io/heiher/hev-socks5-tunnel:2.13
  securityContext:
    privileged: true
    capabilities:
      add:
        - NET_ADMIN
  resources:
    requests:
      cpu: 250m
      memory: 64Mi
    limits:
      memory: 128Mi
  env:
    - name: SOCKS5_ADDR
      value: gluetun.{{ .Values.applicationsNamespace }}.svc.cluster.local
    - name: SOCKS5_UDP_MODE
      value: tcp
    - name: IPV4_INCLUDED_ROUTES
      value: 0.0.0.0/0
    - name: IPV4_EXCLUDED_ROUTES
      value: |-
        10.42.0.0/16
        10.43.0.0/16
{{- end -}}
