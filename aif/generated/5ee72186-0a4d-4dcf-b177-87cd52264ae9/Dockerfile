FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go http.go loans.go ./
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/aif-loans-summary .

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/aif-loans-summary /aif-loans-summary
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/aif-loans-summary"]
