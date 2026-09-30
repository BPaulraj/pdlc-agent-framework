# Other / uncategorised interfaces

No dedicated pack exists yet for this concern's surface (e.g. a messaging/event consumer
tested standalone, GraphQL, CLI, file-drop integration, mobile-native, hardware/IoT). Fall
back to the base envelope and `guidelines/test-design.md` alone: write `steps` and
`expected_result` in enough plain-language detail that a reader unfamiliar with the
surface still knows exactly what was done and exactly what to check. Don't invent an
`interface_details` shape ad hoc.

If this interface recurs across stories, propose a new pack: copy whichever existing pack
is closest (see `test-design-packs/README.md`) as a starting point, and say so in the
test-design summary so a human notices the gap.
