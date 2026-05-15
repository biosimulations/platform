# Runtime-only image. The Nuxt build (`npm ci && npm run build`) happens
# outside Docker (in CI / on the dev's machine), and this image just copies
# the prebuilt `.output/` directory and runs the Nitro Node server.
#
# Nitro emits `.output/` containing the server entrypoint, all static assets,
# and *bundled* server dependencies under `.output/server/node_modules/`, so
# the output directory is self-contained — no separate `npm ci --omit=dev`
# step needed.
#
# Build context: the `frontend/` directory. See `.dockerignore` — only
# `.output/` is shipped into the image.

FROM node:22-alpine

WORKDIR /app

COPY .output ./.output

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000

EXPOSE 3000

USER node

CMD ["node", ".output/server/index.mjs"]
