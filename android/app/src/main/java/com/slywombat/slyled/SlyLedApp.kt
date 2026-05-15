package com.slywombat.slyled

import android.app.Application
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class SlyLedApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Install the global crash handler first thing — anything that
        // throws after this point leaves a retrievable report.
        CrashReporter.install(this)
    }
}
