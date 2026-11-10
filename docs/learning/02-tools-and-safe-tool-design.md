# 02 · Tools, and how to design them so an agent can't do damage

*Milestone 2. Code: `src/agentqa/tools.py`, `policy.py`, `world.py`. Tests: TOOL-01..09.*

## 1. What a tool is, from the model's side

The model never sees our Python. It sees a **definition** for each tool: a name, a description
in plain English, and a JSON schema for the arguments. For example (simplified):

```json
{
  "name": "propose_payment",
  "description": "Propose paying one invoice. This does NOT move money: a human approves every payment...",
  "strict": true,
  "input_schema": {
    "type": "object",
    "properties": {
      "invoice_id": {"type": "string"},
      "pay_to": {"type": "string"},
      "amount_usdc": {"type": "string"}
    },
    "required": ["invoice_id", "pay_to", "amount_usdc"],
    "additionalProperties": false
  }
}
```

When the model wants to act, it replies with a **tool call**: the tool's name plus arguments
that fit the schema. Our code runs the matching Python function, and the result (JSON text)
goes back to the model as a **tool result**. If something went wrong, the result carries
`is_error: true`, so the model knows and can explain or try something else.

Two practical points:
- **The description is part of the prompt.** The model chooses tools by reading their
  descriptions, so a vague description gets a vague choice. Ours say exactly what each tool
  does *and doesn't* do ("This does NOT move money").
- **`strict: true`** makes the API guarantee the arguments match the schema exactly. That rules
  out missing fields, extra fields and wrong types. It does **not** make the *values* correct:
  a perfectly formatted call can still name the wrong address. That's the policy layer's job.

## 2. The five design rules in this repo

**1. Read freely, write only by proposing.**
The agent has five read tools and one write tool, `propose_payment`. A proposal is a request
for a human, not an action: no money moves (TOOL-02).

**2. The dangerous action is not a tool at all.**
Approving a payment is `World.approve(...)`, a function only a person (or the test) calls.
There is no `approve_payment` tool, so the model can't call it, however it's prompted. TOOL-04
asserts that no tool name offers approval or transfers, and that calling every tool on every
invoice moves nothing.

> This is the strongest safety technique in agent design: **don't give the agent the
> capability**. Every capability you add is something an attacker can try to steer.

**3. Rules live in code: the policy layer.**
`policy.check_payment` enforces six rules on every proposal:

| Rule | Stops |
|---|---|
| `payee_not_registered` | paying any address except the vendor's registered one ("our bank details changed"), the most common real-world invoice fraud |
| `amount_mismatch` | paying a different amount, even one cent |
| `invoice_not_open` | paying a disputed or already-paid invoice |
| `already_proposed` | duplicate payments |
| `vendor_blocked` | paying a vendor under a block |
| `over_limit` / `insufficient_funds` | large or unfunded payments |

Each refusal is **typed** (a `Rule`, not just a message). Tests and evals can then count which
rule stopped which attempt (TOOL-03).

**4. Check again at the moment of truth.**
Between proposal and approval the world can change: a vendor gets blocked, an invoice
disputed. `World.approve` re-runs every rule before money moves (TOOL-06). This is the classic
*time-of-check vs time-of-use* lesson from security, applied to an agent.

**5. Log every attempt, not just every success.**
Every call goes into the `ToolLog`, refused ones included (TOOL-09). The chain only shows what
*happened*, and the policy layer stops bad attempts before they reach it. The log shows what
the model *tried*. A model that tries to pay an attacker ten times and is stopped ten times
is still a problem worth measuring. The red-team milestone (M6) reports both numbers.

## 3. Proving the tests can fail: mutation checks

A suite that passes on the first run proves little until you've seen it fail. Before trusting
TOOL-01..09, we planted four bugs in the policy layer, one at a time:

| Planted bug | Caught by |
|---|---|
| payee check removed | 2 tests (payee-swap and the audit-log test) |
| partial payments allowed (`>` instead of `!=`) | 1 test (amount −1 cent) |
| duplicate check removed | 2 tests |
| no re-check at approval | 2 tests (blocked vendor, disputed invoice) |

Every bug turned the suite red. This is **mutation testing**: you check the tests by checking
that they catch deliberate defects.

## 4. Two design choices worth defending in an interview

**Why is the attacker's address allowlisted on the token?** (decision AQ-D1)
The settlement token refuses transfers to unlisted addresses. If the attacker weren't listed, a
payee-swap would be stopped by the token, and the test couldn't tell whether *our* policy
worked. So the scenario allowlists the attacker on purpose: the refusal can then only come from
the policy layer. It's like testing one guard with the other one switched off.

**Why does the model send amounts as strings like "1250.50"?**
JSON numbers are read as floating point in many systems, where 0.1 + 0.2 ≠ 0.3. Money is
always exact integers internally, and `parse_usdc` turns a decimal string into exact base units
or refuses it (TOOL-08). This is the same lesson as the settlement project's F-16 and TAPI-D3.

## 5. Interview questions

**"How do you stop an AI agent from doing something harmful with its tools?"**
> In layers. First, don't give it the dangerous capability: here, approving a payment isn't a
> tool. Second, enforce the rules in code around every tool that changes state: a policy layer
> with typed refusals, re-checked at the moment of execution. Third, keep a human in the loop for
> anything irreversible. The prompt explains the rules, but it's never the only thing enforcing
> them.

**"What does strict tool use guarantee, and what doesn't it?"**
> It guarantees the arguments match the schema: right fields, right types, nothing extra. It
> doesn't guarantee the values are right. A perfectly formatted call can still name the wrong
> address, so you still validate the values in code.

**"How do you know your safety tests actually work?"**
> Mutation checks: I removed each guard in turn and confirmed the suite went red. A safety test
> that stays green when the guard is deleted isn't protecting anything.
