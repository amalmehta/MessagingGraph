# Messaging Graph

A private app for your Mac that reads your Messages history and helps you keep up with the
people in it: who you've drifted from, plans that never got a date, messages worth sending, and
how your friends connect to each other. It runs entirely on your computer.

```mermaid
flowchart LR
    A[Messages history] -->|mg import| V[(Encrypted vault<br/>key in Keychain)]
    B[Instagram export zip] -->|mg import-social| V
    V --> C[Analysis<br/>people, plans, connections]
    C --> D[Local app in your browser<br/>or the Mac app]
```

**[Instructions →](docs/INSTRUCTIONS.md)** · [File structure](docs/FILE-STRUCTURE.md) · [How it works](docs/how-it-works.md)
