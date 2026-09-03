# Gradle wrapper

`gradle-wrapper.jar` is a binary and is not checked in here. Generate it once,
from this directory's parent (`quest/`), with a Gradle 8.x you already have:

    gradle wrapper --gradle-version 8.7

or simply open `quest/` in Android Studio, which writes the wrapper on the
first sync. `gradle-wrapper.properties`, `gradlew` and `gradlew.bat` are already
in place, so only the jar is missing.
