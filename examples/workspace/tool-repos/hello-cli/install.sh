#!/usr/bin/env bash
# Bootstrap script for hello-cli
# Available env vars: INSTALL_PATH, TOOL_VERSION, TOOL_NAME
echo "Running ${TOOL_NAME} bootstrap for version ${TOOL_VERSION}..."
chmod +x "${INSTALL_PATH}/bin/hello"
echo "Bootstrap complete: ${INSTALL_PATH}"
