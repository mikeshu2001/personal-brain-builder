# Personal Brain Builder

[![Tests](https://github.com/mikeshu2001/personal-brain-builder/actions/workflows/tests.yml/badge.svg)](https://github.com/mikeshu2001/personal-brain-builder/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A local-first, agent-guided system for turning owner-approved files and past
work into a private, cross-project memory. It combines an orchestration skill
for AI assistants with a deterministic Python control plane for permissions,
state transitions, validation, reversible connections, and crash-safe local
history.

This repository is the **builder**, not anyone's personal memory. It contains
an empty template and synthetic tests; the generated memory lives in a
separate private directory selected by its owner.

## 60-second overview

1. The owner opens this repository with an AI assistant that can work with
   local files.
2. The assistant follows one dispatcher skill and explains every new class of
   access before asking for explicit permission.
3. Discovery starts with filenames and metadata. File contents are considered
   only after a separate, scoped approval.
4. The language model handles meaning and ambiguity; `brainctl.py` handles the
   operations that should be deterministic: checkpoints, exact write sets,
   source-integrity checks, card validation, connection previews, and undo.
5. Progress is persisted as a state machine, so a long build can stop and
   resume without relying on chat history.
6. The finished memory includes its own small runtime and local history. It is
   not configured to publish or sync anywhere.

The core design choice is deliberate: use an agent for interpretation, but
use code for safety invariants and repeatable filesystem mutations.

## Architecture

```text
owner + file-capable AI assistant
                |
                v
       SKILL.md dispatcher
                |
       +--------+---------+
       |                  |
       v                  v
policy references   Python control plane
                         |
          +--------------+---------------+
          |              |               |
          v              v               v
    state_control   card_engine   portable_history
          |              |               |
          +--------------+---------------+
                         |
                         v
          separate private memory directory
```

| Component | Responsibility |
| --- | --- |
| [`SKILL.md`](skills/build-personal-brain/SKILL.md) | Routes first-run, resume, audit, connection, acceptance, and maintenance work; loads only the references needed for the current stage. |
| [`references/`](skills/build-personal-brain/references) | Defines the user dialogue, safety boundaries, operational state machine, audit protocol, memory model, connection protocol, and acceptance criteria. |
| [`brainctl.py`](skills/build-personal-brain/scripts/brainctl.py) | Provides the deterministic CLI for initialization, scoped inventory, consent records, validation, history, and bounded host connections. |
| [`state_control.py`](skills/build-personal-brain/scripts/state_control.py) | Validates durable state, legal transitions, permission scope, resume points, and the final readiness gate. |
| [`card_engine.py`](skills/build-personal-brain/scripts/card_engine.py) | Validates and searches knowledge cards, including provenance, authority, freshness, secret detection, and supersession chains. |
| [`portable_history.py`](skills/build-personal-brain/scripts/portable_history.py) | Implements dependency-free, content-addressed local history with exact-path saves, locking, atomic publication, undo, and interrupted-operation recovery. |
| [`brain-template/`](skills/build-personal-brain/assets/brain-template) | Supplies an empty canonical memory layout and a self-contained maintenance runtime; it contains no example person or project data. |

For a longer walkthrough, see the
[package architecture](outputs/package-architecture-ru.md) (Russian).

## Safety model

The system is built around fail-closed boundaries:

- metadata discovery and content reading are separate permissions;
- approved roots are exact, recorded scopes, and exclusions can narrow them;
- recursive discovery does not follow symbolic links;
- known credential locations and secret-shaped values are blocked;
- source folders remain read-only during the build and are fingerprinted for
  unexpected change;
- host-instruction changes are bounded, previewed, token-confirmed, and
  reversible;
- local history saves only the exact reviewed path set and supports recovery
  after an interrupted undo;
- current owner instructions outrank stored knowledge, while closed cards are
  never returned as current facts.

See [SECURITY.md](SECURITY.md) and [PRIVACY.md](PRIVACY.md) for the operating
boundaries and responsible-reporting guidance.

## Verification

The test suite uses only Python's standard library. At this revision, all
**81 tests pass locally on Python 3.9.6**:

```bash
python -m unittest discover -s tests -v
```

The suite exercises:

- the complete guided CLI path from initialization to a ready memory;
- state-transition and consent gates, including pause/resume behavior;
- scope normalization, exclusions, source mutation, and link attacks;
- card schema, provenance, authority ordering, secret detection, and
  supersession failures;
- exact-path history saves, byte-and-mode restoration, stale locks, and crash
  recovery;
- connection preview/apply/remove round trips and stale-confirmation refusal.

[GitHub Actions](.github/workflows/tests.yml) runs the same suite on Python
3.9, 3.11, and 3.13. There are no third-party runtime or test dependencies to
install.

## Try it

Clone or download the repository, open the folder in an AI application with
local file access, and send this prompt:

> Read `START-HERE.md` and the instructions in this downloaded folder. Guide
> me step by step through building a shared personal memory. You may read the
> downloaded folder itself. Explain and ask for my explicit permission before
> opening any other folders, reading my documents, or changing assistant
> settings.

The assistant should stop immediately if it cannot work with local files. No
source folder, personal document, generated memory, or assistant setting is
opened or changed merely by reading this repository.

## Repository map

```text
.
|-- START-HERE.md                  # one-prompt entry point
|-- AGENTS.md / CLAUDE.md / ...   # thin runner adapters
|-- skills/build-personal-brain/
|   |-- SKILL.md                   # orchestration dispatcher
|   |-- references/                # staged operating policies
|   |-- scripts/                   # deterministic control plane
|   `-- assets/brain-template/     # empty generated-memory template
|-- tests/                         # 81 synthetic unittest cases
|-- SECURITY.md
`-- PRIVACY.md
```

## Current limitations

- The workflow requires an AI application that can read and write local files.
- Approved text may still be processed by the AI application's remote service;
  the assistant must explain what it can determine before content access.
- Connection support differs between AI applications and may require a manual
  final step.
- After setup, the used checkout contains an ignored private pointer
  (`.personal-brain-pointer.json`) with the generated memory's local path.
  Inspect and remove it before manually archiving or sharing the used checkout.
- This repository provides the builder and validation machinery, not hosted
  storage, cloud sync, or a semantic vector database.

## Русская инструкция

Эта папка помогает создать отдельную память о вас и вашей работе. Будущие
разговоры смогут учитывать ваши проекты, решения, предпочтения и связи между
разными направлениями.

Вам не нужно уметь программировать или заранее наводить порядок в документах.
Помощник проведёт вас по этапам и объяснит каждое действие до его выполнения.

### Как начать

Скопируйте ссылку на репозиторий и отправьте её нейросети, которая умеет
работать с файлами на вашем компьютере:

> Вот ссылка на систему общей памяти:
> https://github.com/mikeshu2001/personal-brain-builder
>
> Открой этот репозиторий и проведи меня по всем этапам создания общей памяти.
> Объясняй простыми словами, что ты собираешься сделать, и спрашивай моё
> разрешение перед чтением других папок или изменением настроек.

Помощник сначала посмотрит только названия, виды, даты изменения и примерные
размеры файлов, покажет план и лишь после отдельного разрешения начнёт читать
содержимое. Рабочие документы не будут переноситься, переименовываться или
переписываться. Работу можно поставить на паузу и продолжить позже.

Важно:

- вы сами выбираете папки, которые можно изучать;
- хранилища паролей и данные для входа не открываются;
- при неожиданной встрече с чувствительными данными чтение прекращается, а
  найденное значение не сохраняется;
- если AI-программа обрабатывает ответы через интернет, разрешённые тексты
  могут отправляться её сервису;
- готовая личная память остаётся в отдельной частной папке и не публикуется
  этим проектом;
- после настройки в использованной копии появится игнорируемый приватный
  указатель `.personal-brain-pointer.json` с локальным путём к памяти; перед
  ручной отправкой или архивацией такую копию нужно проверить и удалить
  указатель.

Подробный пользовательский вход: [START-HERE.md](START-HERE.md).

## Об авторе

Меня зовут Михаил Шумовский. Я автор телеграм-канала
[«Миша, давай по новой»](https://t.me/misha_davai_po_novoi), главный редактор
[«Нейромедиа»](https://neiro.unisender.com/) и помогаю людям осваивать
нейросети в клубе [«Нейроцех»](https://neurozeh.ru/).

## License

[MIT](LICENSE)
