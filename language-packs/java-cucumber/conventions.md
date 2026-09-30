# Java + Cucumber-JVM pack: default conventions

Use these only where the repository has no established convention (check `repo-profile.yaml` first).

## Layout (Maven/Gradle)
- Features: `src/test/resources/features/<domain>/<capability>.feature`
- Step definitions: `src/test/java/<base.pkg>/steps/<Domain>Steps.java`
- Page objects: `src/test/java/<base.pkg>/pages/<Screen>Page.java`
- API clients: `src/test/java/<base.pkg>/api/<Resource>Client.java`
- Test data builders: `src/test/java/<base.pkg>/testdata/<Entity>Builder.java`
- Hooks: `src/test/java/<base.pkg>/hooks/`
- Shared state: `src/test/java/<base.pkg>/context/ScenarioContext.java` (via PicoContainer / Spring / Guice, whichever the repo uses)

## Step definitions
- Use Cucumber expressions (`{string}`, `{int}`, `{double}`) over regex unless the repo uses regex.
- Annotations come from `io.cucumber.java.en.*`.
- Constructor injection of context and page objects. No static state.
- Steps stay thin: they parse parameters, call page objects or clients, and assert.
- Assertions: AssertJ `assertThat(actual).as("business expectation").isEqualTo(expected)`, or the repo's library.

## Page objects
- One class per screen or significant component. Expose intention-level methods (`applyCoupon(code)`), not raw element access.
- Locators are `private final By` fields (Selenium) or `Locator` (Playwright), preferring `data-testid` or roles.
- Waits: explicit (`WebDriverWait` + `ExpectedConditions`, or Playwright auto-wait). Never `Thread.sleep`.
- Methods that navigate return the next page object.

## Style
- Java 17+ features only if the build targets them.
- Match the repo's formatter (Google/Palantir/IDE defaults). 4-space indent is the default.
- Loggers: SLF4J `private static final Logger LOG = LoggerFactory.getLogger(X.class);` if the repo logs.

## Typical checks (put verified versions in config `automation.checks`)
- compile: `mvn -q -DskipTests test-compile` or `gradle testClasses -q`
- dry run: `mvn -q test -Dcucumber.execution.dry-run=true -Dcucumber.filter.tags="{tags}"`
- run new: `mvn -q test -Dcucumber.filter.tags="{tags}"` (add `-Dtest=<RunnerClass>` if the repo uses a runner)
