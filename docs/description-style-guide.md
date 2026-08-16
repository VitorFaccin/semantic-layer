# Description style guide — writing metadata the AI can trust

An LLM agent picks measures, dimensions and views **only from their metadata** (served via REST
`/meta`). Descriptions are therefore a contract, not decoration. This guide is the standard every
member in `model/` follows.

**Language:** descriptions are in **Portuguese** (it matches how business users phrase questions).
Field names stay Cube keywords. Use **"rep"** for the sales representative and **"grão"** (never
"granularidade").

## The four fields and what goes where

| Field | Seen by | Use for |
|---|---|---|
| `title` | UI + agent | Short canonical business label, ≤ 4 words, PT. Ex.: `title: "Comissão total"`. |
| `description` | UI + agent | The rich prose (templates below). **Must be self-sufficient** — the consumer may never read `meta`. 50–200 words / 1–4 sentences. |
| `format` | UI + agent | Every numeric measure: `currency`, `percent` (ratios), `number` (counts). IDs/strings: omit. |
| `meta` | agent only (forwarded by the MCP gateway's `describe_view`; **absent** from `list_metrics` and from the SQL API) | Reinforcement, never the *only* home of a critical fact. **Structured keys:** `granularity`, `additive`, `synonyms`, `prefer`, `avoid`, `coverage`, `values`, `applies_to`. **Prose key:** `ai_context`. |

> Why `description` must repeat what `meta` says: the discovery step (`list_metrics`) exposes only
> member **names**, and SQL API consumers never receive `meta` at all. Mirror critical facts into
> `meta` as structured reinforcement — not the reverse.

### `meta.applies_to` — scope of a member inside a transversal cube

`applies_to: ["<product_line>", …]` marks a member as **line-specific inside a TRANSVERSAL cube**
(e.g. an assembly attribute that only Furniture rows populate; NULL elsewhere).

- **Only on transversal cubes** (`model/cubes/commercial/`), and only on members that do not apply
  to every product line. The `description` must state the same fact in prose ("Aplica-se apenas a
  X; NULL nas demais linhas") — `meta` is reinforcement, never the only home.
- **Absence is not ambiguous.** On a transversal cube it means "applies to all lines". On a
  **vertical** cube/view it is never needed: the whole cube is single-line and the scope is stated
  in the CUBE and VIEW `description`, which the agent reads before picking members.

### `meta.values` — value glossary for categorical dimensions

Value-listing tools return **bare values**, and a `description` defines the *member*, not its
*values*. Without a glossary the agent can filter by a code it cannot explain — or invent one that
looks plausible.

- **Applies to** every **low-cardinality** categorical dimension (roughly ≤ 15 distinct values):
  status, tier, type, channel, stage, reason.
- **Exempt:** high-cardinality dimensions (ids, names, cities) and free text. Listing them is noise.
- **The rule:** the `description` must **define** each value, and the same map goes to
  `meta.values: {value: meaning}`.
- **Literal spelling wins.** Write the value exactly as it exists in the warehouse, and call out the
  trap when the spelling is surprising — the agent filters on the literal, not on the concept.

```yaml
- name: tier
  title: "Tier"
  description: "Tier do representante na rede. Valores LITERAIS no dado (exatamente 3): 'Independent' (rep autônomo — NÃO é agência), 'Associate' e 'Agency'. AGÊNCIA = 'Associate' + 'Agency'; não existe um valor único cobrindo os dois. Para CONTAR agências use a measure agencies_count (company_id distinto), pois uma agência tem vários reps. String."
  meta:
    values:
      "Independent": "rep autônomo; não é agência"
      "Associate": "agência associada"
      "Agency": "agência de maior porte"
```

### `meta.coverage` — required whenever NULL is normal

The agent can only warn about a sparse column if the layer **carries that fact**. Left empty, a
mostly-NULL column reads as if it were complete.

- **Definition of done for any new member:** if "NULL is normal" for it, `coverage` is stated.
- **Populate it wherever absence is structural:** line-specific columns on transversal cubes (pair
  with `applies_to`), late-arriving fields, sparsely filled attributes, columns that only exist
  after a given date, and **fields that are NULL by semantics** (a loss reason on a deal that was
  not lost).
- **Say it in prose too** — `meta` never reaches the SQL API.

```yaml
meta:
  coverage: "preenchido apenas em negócios com status 'lost'"
```

### The NULL-as-zero trap

Worth its own note, because it is the failure mode that produces a *confidently wrong* answer
rather than an error: when a source stores "zero" as **NULL**, a filter like `measure = 0` returns
**no rows**, and the agent reports "nenhum" instead of "todos os que fizeram zero".

Wherever a mart writes zero as NULL, say so explicitly in `coverage` **and** tell the agent to use
the `notSet` operator instead of `equals: 0`. Then pin it with a golden query — this is exactly the
class of bug that no schema check catches.

## `meta.ai_context` — agent-only instruction

`ai_context` is a **free-prose** key inside `meta`, meant for the agent and never rendered in a UI.
The name is Cube's own key, so we adopt it rather than coining one.

| Level | Consumed? |
|---|---|
| measure / dimension | ✅ forwarded — the gateway maps `meta` 1:1 into `describe_view` |
| view | ⏳ depends on the gateway forwarding view-level `meta`; harmless but invisible until it does |
| cube | ❌ never — private cubes are absent from `/meta` |

> The consuming agent's system prompt must be told to READ `meta.ai_context`. It is prose inside a
> free-form key, not a protocol field.

### Structure before text

The industry converges on the same order of preference — Cube's own guidance, Snowflake Cortex
Analyst instructions and Databricks Genie all state it: text instructions are the **last** resort.

1. **Structured semantics** — a `measure`, a `dimension`, a `description`, a structured `meta` key
2. **A named SQL expression** — a `segment` (a filter that cannot be misread)
3. **A verified example** — a golden query
4. **Free prose** — `ai_context`

Before writing a line, ask: *should this be a segment, a measure, or a `meta` key instead?* If it
should, the prose is a patch over a modelling gap.

### What must NOT go in `ai_context`

Duplicating a fact across two homes is how the two homes drift apart. One fact, one owner:

| Fact | Owner | In `ai_context`? |
|---|---|---|
| Grain of a row | `meta.granularity` | ❌ |
| Additive or not | `meta.additive` | ❌ |
| Phrasings users type | `meta.synonyms` | ❌ |
| "para X use `{outro_membro}`" | `meta.avoid` / `meta.prefer` | ❌ |
| NULL / partial population | `meta.coverage` | ❌ |
| Line scope of a member | `meta.applies_to` | ❌ |
| Meaning of each categorical value | `meta.values` | ❌ |
| Unit / currency / percent | `format` | ❌ |
| Answer formatting, tone | the agent's system prompt | ❌ |
| **How to read a value: present / zero / missing row** | — | ✅ |
| **The quantified reason behind a prohibition** | `avoid` says *what*, not *why* | ✅ |
| **What to ask back when the question is ambiguous** | — | ✅ |
| **Which sibling view to prefer for a class of question** | — | ✅ (view level) |

### Template — view level (3 slots)

```yaml
views:
  - name: commissions
    description: "..."          # everyone sees this; stays self-sufficient
    meta:
      ai_context: >
        LEITURA: cada linha é uma parcela de comissão; um valor é o quanto aquela PARCELA
        representa, não o total da venda. Zero em liquidado = parcela ainda não paga, não
        comissão inexistente. Linha ausente = sem registro de comissão no recorte — não
        conclua inexistência de produto/rep/cliente.
        DESAMBIGUAÇÃO: "quanto de comissão o rep recebeu?" é ambíguo — o total devido está em
        sales; o que já foi pago está aqui. Na dúvida pergunte "devida ou liquidada?".
        NUNCA: somar o valor de comissão nesta view — ele repete por parcela/pagamento e
        superconta várias vezes em vendas multi-parcela. O total está em sales.
```

- **`LEITURA`** — what a populated value *means*, plus the two readings the agent gets wrong on its
  own: **zero** (a fact) versus **missing row** (no data — never proof of non-existence).
- **`DESAMBIGUAÇÃO`** — the business ambiguity and the question to ask back.
- **`NUNCA`** — a hard prohibition **with its quantified reason**. A prohibition without a number
  gets negotiated away; a measured factor does not.

Omit a slot with nothing real to say. Three empty headings are worse than one useful line.

### Template — member level

One or two imperative sentences, and **only** when the member carries a reading rule no structured
key can express. If `avoid` / `prefer` / `additive` already covers it, write nothing.

```yaml
- name: settled_amount
  meta:
    additive: true
    ai_context: "Dinheiro que JÁ saiu (parcelas liquidadas). Ausência não é 'sem comissão', é 'ainda não liquidado'."
```

### Writing rules

1. **Imperative, addressed to the agent.** "Use", "não use", "nunca some".
2. **One fact per line**, so a stale line can be deleted on its own.
3. **Name the alternative member explicitly.** Without the name the agent picks by name similarity.
4. **A number beats an adjective.** "superconta ~1,7x" > "pode superestimar".
5. **Budget:** view level 40–80 words; member level ≤ 2 sentences. `describe_view` returns EVERY
   member with its `meta` — prose on hundreds of measures crowds out the agent's own context.
6. **Never contradict the `description`.** SQL API consumers see the description and never `meta`;
   two divergent texts means two "correct" numbers.
7. **PT prose**, like every other AI-facing field.

### How to develop it (the loop)

1. **Start from a question the agent got wrong** — real questions are the seed list.
2. **Classify the failure:** missing *structure* or missing *instruction*? Only the second becomes
   `ai_context`.
3. **Write one line, in the right slot.**
4. **Verify it arrives:** `describe_view("<view>")` must return it under `meta`.
5. **Re-ask the question** and read the compiled SQL.
6. **Lock it as a regression** — the question becomes a golden query in CI.

Start small and iterate: a large upfront pile of instructions is harder to maintain and measurably
degrades accuracy.

## What makes a description bullet-proof

1. **Lead with the canonical term, then define what it *is*** — not only how it is computed.
2. **Disambiguate from siblings.** If two members can be confused, each names the other and says
   which to use when: *"Para X use `{outra_measure}`."* This is the #1 fix for wrong picks.
3. **State grão · unidade · aditividade · escopo.** A non-additive measure MUST say `não-aditivo`
   and how to aggregate it.
4. **One synonym sentence** at the end: *"Também chamado de a/b/c."* Mirror it in `meta.synonyms`.
   **No keyword salad** in the prose — it creates near-duplicate vectors and noise.
5. **Imperatives, not vague words.** "Use para…", "Não use para…" beat "útil para", "importante".
6. **50–200 words.** Beyond that the model stops consuming it; shorter risks ambiguity.
7. **Opaque members state how they are formed.** A score, rank or computed flag exposed without its
   derivation is a number the agent can report but cannot justify. One sentence of "como é formado"
   belongs in the `description` — the derivation is part of what the member IS.

## Templates

### Measure
```
[Termo canônico]: [o que é, 1 frase]. [Quando/como usar].
[Quando NÃO usar — "para X use {outra_measure}"]. [grão · unidade · aditividade · escopo].
[Também chamado de a/b/c.]
```
Example — `orders.all_orders_count`:
> "Pedidos (todas as linhas): contagem de TODOS os pedidos, incluindo filhos/duplicados/draft.
> Use apenas para auditoria/reconciliação. Para 'pedidos gerados' use `orders_count` (só raiz).
> Grão de pedido, aditivo. Também chamado de pedidos brutos."

```yaml
- name: all_orders_count
  title: "Pedidos (auditoria)"
  sql: order_id
  type: count_distinct
  format: number
  description: "Pedidos (todas as linhas): contagem de TODOS os pedidos... Para 'pedidos gerados' use orders_count (só raiz). Grão de pedido, aditivo. Também chamado de pedidos brutos."
  meta:
    granularity: "pedido"
    additive: true
    avoid: "para 'pedidos gerados' use orders_count"
    synonyms: ["pedidos brutos", "todas as orders"]
```

### Dimension
```
[Nome]: [o que representa]. [Valores LITERAIS, cada um DEFINIDO — ver meta.values; exceto alta cardinalidade].
[Quando usar / contraste se houver dimensão parecida]. [Tipo/formato/faixa]. [Se data: qual evento marca e fuso.]
```
Listing values is not enough — **define** them. High-cardinality dimensions are exempt.

### Segment (public — a named filter)
A segment is the *name and the governance* of a row condition; it is queried by name, so the caller
can never get the operator or the value wrong. Its description must state **the cut** and **what
falls outside it** — a segment that only says what it includes leaves the complement undefined.

```
[Termo canônico]: [qual corte de linhas define]. [O que fica FORA do corte].
[Use para …]. [Se houver segmento irmão/complementar, nomeie-o.]
```
Example — `reps.agencies`:
> "Agências da rede: reps dos tiers 'Associate' e 'Agency'. FICA FORA o 'Independent', que é o
> autônomo — para ele use o segmento `independent_reps`. Use com qualquer measure para recortar
> produção de agência."

Prefer a segment over prose in `ai_context` whenever the fact is a row condition: a named filter
cannot be misinterpreted, a sentence can.

**Naming — segments share the member namespace.** A segment cannot reuse the name of a measure or
dimension in the same cube; Cube fails to compile with `<name> defined more than once`. This bites
exactly where it is most tempting: a boolean column `approved` and a segment for "the deals where
it is true" want the same word. Name the segment for the **row set**, not the column —
`approved_deals`, not `approved`.

### Cube (private)
Entity + grain + scope (all product lines vs a single one) + how to slice it (`product_line`).

### View (public — what the agent sees)
Must be **self-sufficient** and say *when to choose this view over the alternative*. Example —
`operational_furniture`:
> "Operacional da esteira de Móveis… Específico da linha, sem equivalente transversal. Para
> vendas/comissão de qualquer linha use `sales`/`commissions`."

## Non-additive members — always flag

`avg_*`, `median_*`, rate ratios and rolling/reference values are non-additive. The description must
say `não-aditivo` and how to aggregate ("agregue por rep, não some entre meses"), plus
`meta.additive: false`. This is enforced by `test_non_additive_measures_are_flagged`.

## Confusable pairs that MUST cross-reference each other

- a vertical view (`operational_*`, `ranking_rep_*`) vs the transversal one (`sales`, `orders`,
  `commissions`) — say which is canonical for the question.
- `orders.orders_count` ↔ `orders.all_orders_count` ↔ `orders.child_orders_count`
- `sales.total_sale_amount` (venda, R$) ↔ the commission measures (comissão, R$)
- **commission TOTAL** (`sales.total_commission_amount`, **sale grain**, additive) ↔ **margin rate**
  (`commissions.margin_rate`, commission × installment grain). Never `SUM` a commission amount on
  the `commissions` side — the amount repeats per installment row and over-counts. The ratio is
  still correct there, because numerator and denominator share the same grain and the repetition
  cancels; the *total* is only correct at sale grain.
- `reps.reps_count` (reps) ↔ `reps.agencies_count` (distinct `company_id`) — an agency has many reps.
