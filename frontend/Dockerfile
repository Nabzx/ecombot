# AgentOps frontend image (development-oriented).
# Dependencies are installed into /app/node_modules; in Compose the source is bind
# mounted and node_modules is preserved with a named volume so hot reload works.

FROM node:20-alpine

WORKDIR /app

# Install dependencies first for layer caching. Use the lockfile for reproducibility.
COPY package.json package-lock.json ./
RUN npm ci

# Application source (overlaid by a bind mount in development).
COPY . .

EXPOSE 3000

# Compose overrides host/port; this is the standalone default.
CMD ["npm", "run", "dev"]
