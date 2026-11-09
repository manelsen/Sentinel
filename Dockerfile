FROM golang:1.22 AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY cmd ./cmd
COPY sidecar ./sidecar

RUN CGO_ENABLED=1 go build -o /out/sentinel ./cmd/sentinel

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /out/sentinel /usr/local/bin/sentinel

EXPOSE 8080

ENTRYPOINT ["sentinel"]
CMD ["serve", "--db", "/data/sentinel.db", "--host", "0.0.0.0", "--port", "8080"]
