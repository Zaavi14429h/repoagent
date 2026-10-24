FROM node:22-bookworm-slim

RUN npm install --global jest eslint typescript
USER node
WORKDIR /workspace
ENTRYPOINT []

