package com.slywombat.slyled.di

import android.content.Context
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.data.repository.ServerPreferences
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {
    @Provides
    @Singleton
    fun provideOkHttp(): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .addInterceptor(HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        })
        .build()

    // #804 — singleton mic-driven auto-brightness driver. Survives screen
    // rotation; lifecycle is owned by LiveStageViewModel start()/stop().
    // Takes ServerPreferences so tunables persist across app restart.
    @Provides
    @Singleton
    fun provideMicAutoBrightness(
        @ApplicationContext ctx: Context,
        prefs: ServerPreferences,
    ): MicAutoBrightness = MicAutoBrightness(ctx, prefs)
}
