# Messaging Graph

A private app for your Mac that reads your Messages history and helps you keep up with the
people in it: who you've drifted from, plans that never got a date, messages worth sending, and
how your friends connect to each other. It runs entirely on your computer. $5 once, with a
14-day free trial.

```mermaid
flowchart LR
    A[Messages history] -->|import, with Full Disk Access| V[(Encrypted vault<br/>key in Keychain)]
    V --> C[Analysis<br/>people, plans, connections]
    C --> D[The Mac app's window<br/>or your browser]
    L[Licence + update checks<br/>nothing from your messages] -.->|network gate| N((Internet))
```

**[Instructions →](docs/INSTRUCTIONS.md)** · [System design](docs/SYSTEM-DESIGN.md) · [File structure](docs/FILE-STRUCTURE.md) · [Go-live](docs/GO-LIVE.md) · [How it works](docs/how-it-works.md) · [Product site](site/index.html)
