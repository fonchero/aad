FROM golang:1.24.13-bookworm AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go test -mod=readonly ./... && \
    CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/customer-loans .

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=build /out/customer-loans /customer-loans
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/customer-loans"]
