FROM node:20-alpine

WORKDIR /app

COPY apps/web/package.json apps/web/yarn.lock* ./
RUN yarn install --frozen-lockfile

COPY apps/web/ ./

CMD ["yarn", "dev", "--host", "0.0.0.0"]
