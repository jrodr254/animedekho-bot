FROM node:20-slim

WORKDIR /app

# Copy the local modified source code
COPY ./src-code .

# Install dependencies
RUN npm install

# Expose port
EXPOSE 4030

CMD ["npx", "tsx", "."]
