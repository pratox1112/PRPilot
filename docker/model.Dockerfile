# Cloud Run `model`: the student GGUF on llama.cpp's OpenAI-compatible server, CPU only.
# Same llama.cpp build (b11960, the v0.8.0 commit 8642541b) the GGUF was re-tested with.
# The build context is models/, which holds the GGUF copied from Explorer:
#
#   docker build -f docker/model.Dockerfile -t <registry>/model:<git sha> models
#
# One request at a time per instance (-np 1, Cloud Run concurrency 1), so a long prompt gets
# the whole 16,384-token context. LLAMA_ARG_THREADS is set to the instance's vCPUs at deploy.

FROM ghcr.io/ggml-org/llama.cpp:server-b11960@sha256:8052c13edd987de8858f8150031e025ac3d66823f09cf3ec8220899c4abce220
COPY student-fill-1016-Q4_K_M.gguf /models/student.gguf
RUN echo "b6e18672b4043587baa67c907d120c7ab02f719ec20c99b50e48ce7fa8ff53bb  /models/student.gguf" \
    | sha256sum -c -
ENV LLAMA_ARG_HOST=0.0.0.0 LLAMA_ARG_PORT=8080
EXPOSE 8080
ENTRYPOINT ["/app/llama-server"]
CMD ["-m", "/models/student.gguf", "-a", "prpilot-model", "--jinja", "-c", "16384", "-np", "1"]
