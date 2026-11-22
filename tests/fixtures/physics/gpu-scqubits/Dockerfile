# Private discovery image. Base is the immutable dependency image from TASK-005.
ARG DISCOVERY_BASE
FROM ${DISCOVERY_BASE}
USER root
COPY probe.py cases.json /opt/wqs-scqubits/
USER 1001:1001
ENTRYPOINT ["/opt/discovery/bin/python", "/opt/wqs-scqubits/probe.py"]
