---
description: Every work the method and the modules cite, under its key.
---

# References

The works the method and the modules cite, each under the key a text cites it by: the
surnames of its one or two authors, or its first author's with `et al.`, and the year. A work
read in a preprint names the version read, an arXiv link its version, and a place cited in it,
a section or a numbered statement, is that version's.
`tools/docs_lint.py` holds this list and the citations in the tree equal, in both
directions.

## Planning under nondeterminism

- **Cimatti et al. 2003.** A. Cimatti, M. Pistore, M. Roveri, P. Traverso. Weak, strong,
  and strong cyclic planning via symbolic model checking. Artificial Intelligence 147,
  2003, 35-84. [doi:10.1016/S0004-3702(02)00374-0](https://doi.org/10.1016/S0004-3702(02)00374-0)
- **Bertoli et al. 2006.** P. Bertoli, A. Cimatti, M. Roveri, P. Traverso. Strong planning
  under partial observability. Artificial Intelligence 170, 2006, 337-384.
  [doi:10.1016/j.artint.2006.01.004](https://doi.org/10.1016/j.artint.2006.01.004)
- **Daniele et al. 2000.** M. Daniele, P. Traverso, M. Y. Vardi. Strong cyclic planning
  revisited. European Conference on Planning (ECP 1999), Springer, 2000.

## Abstraction over Boolean algebras

- **Graf and Saidi 1997.** S. Graf, H. Saidi. Construction of abstract state graphs with
  PVS. CAV 1997.
- **D'Antoni and Veanes 2017.** L. D'Antoni, M. Veanes. The power of symbolic automata and
  transducers. CAV 2017.

- **Kupferman and Vardi 1999.** O. Kupferman, M. Y. Vardi. Vacuity detection in temporal model
  checking. CHARME 1999, LNCS 1703, 82-98.
  [doi:10.1007/3-540-48153-2_8](https://doi.org/10.1007/3-540-48153-2_8). The extended version
  is in International Journal on Software Tools for Technology Transfer 4(2), 2003, 224-233,
  [doi:10.1007/s100090100062](https://doi.org/10.1007/s100090100062), whose numbering is not
  the one cited here.
- **Bauer et al. 2011.** A. Bauer, M. Leucker, C. Schallhart. Runtime verification for LTL and
  TLTL. ACM Transactions on Software Engineering and Methodology 20(4), 2011.
  [doi:10.1145/2000799.2000800](https://doi.org/10.1145/2000799.2000800), read in the authors'
  manuscript. A monitor decides a property on a prefix only by a bad or a good one, which a
  pending state is not.
- **Roşu and Chen 2012.** G. Roşu, F. Chen. Semantics and algorithms for parametric monitoring.
  Logical Methods in Computer Science 8(1:09), 2012. Parametric trace slicing: the instances a
  run and a proxy hold, and those the walk holds of each value.
- **Fink et al. 2008.** S. J. Fink, E. Yahav, N. Dor, G. Ramalingam, E. Geay. Effective typestate
  verification in the presence of aliasing. ACM Transactions on Software Engineering and
  Methodology 17(2), 2008, read in the authors' copy. A strong update needs one concrete object,
  and a focus splits a summary by the object an operation reads.
- **Naeem and Lhoták 2008.** N. A. Naeem, O. Lhoták. Typestate-like analysis of multiple
  interacting objects. OOPSLA 2008, 347-366.
  [doi:10.1145/1449764.1449792](https://doi.org/10.1145/1449764.1449792). An object read as the
  set of names holding it, which tells may and must alias apart at once.
- **Balakrishnan and Reps 2006.** G. Balakrishnan, T. Reps. Recency-abstraction for
  heap-allocated storage. SAS 2006, LNCS 4134, 221-239.
  [doi:10.1007/11823230_15](https://doi.org/10.1007/11823230_15), read in the authors' copy. The
  object made last is one, and those made before it a summary.

## Records and their fields

- **Castagna 2023.** G. Castagna. Typing records, maps, and structs. Proc. ACM Program. Lang.
  7 (ICFP), 2023, article 196. [doi:10.1145/3607838](https://doi.org/10.1145/3607838).
  Selection of a field the record's type proves defined, and the projection that types it;
  open and closed record types.
- **Attouche et al. 2024.** L. Attouche, M.-A. Baazizi, D. Colazzo, G. Ghelli, C. Sartiani,
  S. Scherzinger. Validation of modern JSON Schema: formalization and complexity. Proc. ACM
  Program. Lang. 8 (POPL), 2024, article 49.
  [doi:10.1145/3632891](https://doi.org/10.1145/3632891). Which keys
  `additionalProperties` and `unevaluatedProperties` read: a result read closed.

## Synthesis from typed components

- **Feng et al. 2017.** Y. Feng, R. Martins, Y. Wang, I. Dillig, T. W. Reps.
  Component-based synthesis for complex APIs. POPL 2017. The reachability pruning of
  SyPet.
- **Guo et al. 2022.** Z. Guo, D. Cao, D. Tjong, J. Yang, C. Schlesinger, N. Polikarpova.
  Type-directed program synthesis for RESTful APIs. PLDI 2022.
  [doi:10.1145/3519939.3523450](https://doi.org/10.1145/3519939.3523450). Semantic types
  mined from observed calls, as APIphany.

## Language models as planners and agents

- **Kambhampati et al. 2024.** S. Kambhampati, K. Valmeekam, L. Guan, M. Verma, K. Stechly,
  S. Bhambri, L. Saldyt, A. Murthy. Position: LLMs can't plan, but can help planning in
  LLM-Modulo frameworks. ICML 2024, PMLR 235.
  [arXiv:2402.01817v3](https://arxiv.org/abs/2402.01817v3)
- **Yao et al. 2024.** S. Yao, N. Shinn, P. Razavi, K. Narasimhan. τ-bench: a benchmark for
  tool-agent-user interaction in real-world domains.
  [arXiv:2406.12045v1](https://arxiv.org/abs/2406.12045v1), 2024.
- **Elkoussy and Perez 2026.** L. Elkoussy, J. Perez. AgentLTL: a trace-verification
  framework for measuring, enforcing, and training procedural compliance in tool-using LLM
  agents. [arXiv:2607.02599v1](https://arxiv.org/abs/2607.02599v1), 2026.
- **Kamath et al. 2025.** A. Kamath, S. Zhang, C. Xu, S. Ugare, G. Singh, S. Misailovic.
  Enforcing temporal constraints for LLM agents.
  [arXiv:2512.23738v1](https://arxiv.org/abs/2512.23738v1), 2025. Agent-C, whose rules bind an
  argument's value across two calls, as a policy with `per` does at run time.
- **Mansoor et al. 2026.** I. K. Mansoor, A. Phadke, P. Rana. Verified tool calls improve LLM
  agent reliability under non-atomic failures.
  [arXiv:2608.02645v1](https://arxiv.org/abs/2608.02645v1), 2026. A postcondition read before a
  retry, which the method does not read.

## Information flow and privilege for agents

- **Sah et al. 2026.** T. Sah, V. Srivastava, D. Sah, K. Jordan. The verifier tax: horizon
  dependent safety success tradeoffs in tool using LLM agents. ACM CAIS 2026.
  [arXiv:2603.19328v1](https://arxiv.org/abs/2603.19328v1). Integrity leaks, identifiers made up
  to pass authentication, and deterministic grounding gates against them: a flow rule with
  `only` over a program, and the proxy's check at run time.
- **Beurer-Kellner et al. 2025.** L. Beurer-Kellner, B. Buesser, A.-M. Creţu, E. Debenedetti,
  D. Dobos, D. Fabian, M. Fischer, D. Froelicher, K. Grosse, D. Naeff, E. Ozoani, A. Paverd,
  F. Tramèr, V. Volhejn. Design patterns for securing LLM agents against prompt injections.
  [arXiv:2506.08837](https://arxiv.org/abs/2506.08837), 2025. Plan-then-execute and
  code-then-execute, the order the critic and the runner hold.
- **Nasr et al. 2025.** M. Nasr, N. Carlini, C. Sitawarin, S. V. Schulhoff, J. Hayes, M. Ilie,
  J. Pluto, S. Song, H. Chaudhari, I. Shumailov, A. Thakurta, K. Y. Xiao, A. Terzis,
  F. Tramèr. The attacker moves second: stronger adaptive attacks bypass defenses against LLM
  jailbreaks and prompt injections. [arXiv:2510.09023](https://arxiv.org/abs/2510.09023), 2025.
  Why the proxy's flow rules, read by equality, are no guarantee.
- **Debenedetti et al. 2025.** E. Debenedetti, I. Shumailov, T. Fan, J. Hayes, N. Carlini,
  D. Fabian, C. Kern, C. Shi, A. Terzis, F. Tramèr. Defeating prompt injections by design.
  [arXiv:2503.18813v2](https://arxiv.org/abs/2503.18813v2), 2025. CaMeL.
- **Costa et al. 2025.** M. Costa, B. Köpf, A. Kolluri, A. Paverd, M. Russinovich,
  A. Salem, S. Tople, L. Wutschitz, S. Zanella-Béguelin. Securing AI agents with
  information-flow control. [arXiv:2505.23643v2](https://arxiv.org/abs/2505.23643v2), 2025.
  FIDES.
- **Shi et al. 2025.** T. Shi, J. He, Z. Wang, H. Li, L. Wu, W. Guo, D. Song. Progent:
  securing AI agents with privilege control.
  [arXiv:2504.11703v3](https://arxiv.org/abs/2504.11703v3), 2025. The paper writes a rule as an
  object, its priority, effect, conditions and fallback named (Fig. 9); its code,
  [sunblaze-ucb/progent](https://github.com/sunblaze-ucb/progent) (MIT), writes one as the list
  `(priority, effect, condition, fallback)`, effect 0 allowing and 1 forbidding, fallback 0
  returning a message, 1 ending the run and 2 asking the user, the form this package reads.

## Specifications and prior work

- [Model Context Protocol, revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28):
  a tool's `inputSchema`, `outputSchema` and annotations, and a result's `isError`.
- [JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12), read by
  [valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema).
- valplan, [doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600):
  predicate abstraction over a Boolean algebra of JSON Schema sets with strong planning
  over the cells, the method this package starts from. Its code is not in this tree.
