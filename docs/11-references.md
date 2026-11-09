# References

The works the method and the modules cite, each under the key a text cites it by: the
surnames of its one or two authors, or its first author's with `et al.`, and the year.
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

- **Kupferman and Vardi 2003.** O. Kupferman, M. Y. Vardi. Vacuity detection in temporal model
  checking. International Journal on Software Tools for Technology Transfer 4(2), 2003, 224-233.
  [doi:10.1007/s100090100062](https://doi.org/10.1007/s100090100062)

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
  [arXiv:2402.01817](https://arxiv.org/abs/2402.01817)
- **Yao et al. 2024.** S. Yao, N. Shinn, P. Razavi, K. Narasimhan. τ-bench: a benchmark for
  tool-agent-user interaction in real-world domains.
  [arXiv:2406.12045](https://arxiv.org/abs/2406.12045), 2024.
- **Elkoussy and Perez 2026.** L. Elkoussy, J. Perez. AgentLTL: a trace-verification
  framework for measuring, enforcing, and training procedural compliance in tool-using LLM
  agents. [arXiv:2607.02599](https://arxiv.org/abs/2607.02599), 2026.

## Information flow and privilege for agents

- **Sah et al. 2026.** T. Sah, V. Srivastava, D. Sah, K. Jordan. The verifier tax: horizon
  dependent safety success tradeoffs in tool using LLM agents. ACM CAIS 2026.
  [arXiv:2603.19328](https://arxiv.org/abs/2603.19328). Integrity leaks, identifiers made up
  to pass authentication, and deterministic grounding gates against them: a flow rule with
  `only` over a program, and the proxy's check at run time.
- **Debenedetti et al. 2025.** E. Debenedetti, I. Shumailov, T. Fan, J. Hayes, N. Carlini,
  D. Fabian, C. Kern, C. Shi, A. Terzis, F. Tramèr. Defeating prompt injections by design.
  [arXiv:2503.18813](https://arxiv.org/abs/2503.18813), 2025. CaMeL.
- **Costa et al. 2025.** M. Costa, B. Köpf, A. Kolluri, A. Paverd, M. Russinovich,
  A. Salem, S. Tople, L. Wutschitz, S. Zanella-Béguelin. Securing AI agents with
  information-flow control. [arXiv:2505.23643](https://arxiv.org/abs/2505.23643), 2025.
  FIDES.
- **Shi et al. 2025.** T. Shi, J. He, Z. Wang, H. Li, L. Wu, W. Guo, D. Song. Progent:
  securing AI agents with privilege control.
  [arXiv:2504.11703](https://arxiv.org/abs/2504.11703), 2025.

## Specifications and prior work

- [Model Context Protocol, revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28):
  a tool's `inputSchema`, `outputSchema` and annotations, and a result's `isError`.
- [JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12), read by
  [valgebra-jsonschema](https://github.com/ppigazzini/valgebra-jsonschema).
- valplan, [doi:10.5281/zenodo.23686600](https://doi.org/10.5281/zenodo.23686600):
  predicate abstraction over a Boolean algebra of JSON Schema sets with strong planning
  over the cells, the method this package starts from. Its code is not in this tree.
