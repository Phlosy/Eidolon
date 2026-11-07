# First-Time User Onboarding

After email verification, the founder enters a company with stage `FOUNDING` and zero employees, projects, repositories, documents, runtime instances and company knowledge. Department shells exist only so the first real Employee Onboarding flow has valid organizational destinations.

The company-founding tutorial is a declarative workflow under `app/tutorials/`, not a large React component. The backend persists progress per Human User and evaluates requirements against real domain state:

- CEO and Engineer steps inspect actual employee roles.
- Runtime setup inspects the CEO's persisted RuntimeInstance; Mock remains visibly Mock.
- Project and review steps inspect the structured project, formal review decisions and baselines.
- Development, testing and delivery inspect completed phases and a real DeliveryPackage.

The guide can be paused, resumed, skipped, and restored after logout. Optional Git and QA steps may be deferred without fabricating completion. Finishing every required step changes the company from `FOUNDING` to `OPERATING`; the Tutorial Center remains available in Settings for replayable topic chapters.

The tutorial may explain, navigate, spotlight and observe. It does not insert employees, projects, reviews or documents directly; those operations remain owned by their existing business services.
