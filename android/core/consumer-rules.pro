# kotlinx.serialization models used by Retrofit (only relevant if an app enables R8)
-keepattributes *Annotation*, InnerClasses, Signature, Exceptions
-keep,includedescriptorclasses class ca.nimaste.newman.core.**$$serializer { *; }
-keepclassmembers class ca.nimaste.newman.core.** { *** Companion; }
-keepclasseswithmembers class ca.nimaste.newman.core.** { kotlinx.serialization.KSerializer serializer(...); }
-keep interface ca.nimaste.newman.core.api.NewmanApi { *; }
