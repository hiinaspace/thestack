# the stack (WIP)

A harness for LLMs to play Magic: the Gathering against each other, and a web viewer
for game transcriptions for humans to follow along with the LLM's internal thoughts.

As the saying goes, this is "for entertainment or educational purposes". There
are definitely better ways to get computers to play magic well, but I'm hoping
LLMs playing magic badly is at least more entertaining, a la "Claude plays
Pokemon". To this end there's also an LLM commentator that reads the game state
every turn and tries to provide color.

While in theory LLMs could play MTG like we do (read the rules, say what you're
doing, challenge the opponent if you think they're doing something wrong and
call a judge/look stuff up on the internet), I don't quite trust them to do this
yet. So this harness uses
[argentum-engine](https://github.com/wingedsheep/argentum-engine) to enforce the
rules, and also provides the LLM with a list of legal actions to choose from
(plus some combat math estimation).

## Status

It works on my machine. Gemma4:e4b running locally can at least play simple decks and be entertaining,
but it'll still choose questionable actions a lot of the time.

## Architecture

Pretty much basic prompt engineering, with the rough style pulled from the
current mid-2026 era "agent" frameworks. No RAG or anything fancy, just tool
calls into the game engine, oracle text, and some basic MTG tips.

There's also some slopumentation in [CHARTER.md](CHARTER.md) on the general posture of this.

## uh so can I use it

In current month you could definitely point this repo at your slop bot of choice
and say "make this work on my machine" and it'll edit the configs and pull
argentum engine and run it locally for you.

But no it's not packaged for reuse yet. maybe later.
