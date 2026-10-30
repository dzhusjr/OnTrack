import Foundation

/// What a person reads aloud while OnTrack learns their voice, one passage per language.
/// About 80 words each: a calm 30 seconds. Plain sentences, a few numbers, varied sounds.
public enum EnrollmentText {
    public struct Language: Equatable, Sendable {
        public let code: String
        public let name: String
        public let passage: String
    }

    public static let all: [Language] = [
        Language(code: "en", name: "English", passage: """
            Every morning I check the news, answer a few messages, and plan what needs to get done before lunch. \
            Today we are talking about a new project, its deadlines, and who will take care of each part. \
            If something is unclear, please ask. I usually speak at a calm pace, and sometimes faster when I am excited. \
            Numbers like thirty-five, one hundred, and two thousand twenty-six should sound natural too. \
            Now, let me tell you what I think about the next steps.
            """),
        Language(code: "nl", name: "Dutch", passage: """
            Elke ochtend lees ik het nieuws, beantwoord ik een paar berichten en bedenk ik wat er voor de lunch klaar moet zijn. \
            Vandaag praten we over een nieuw project, de deadlines en wie welk deel op zich neemt. \
            Als iets niet duidelijk is, vraag het dan gerust. Meestal praat ik rustig, soms sneller als ik enthousiast ben. \
            Getallen zoals vijfendertig, honderd en tweeduizend zesentwintig moeten ook gewoon klinken. \
            Dan vertel ik nu wat ik van de volgende stappen vind.
            """),
        Language(code: "uk", name: "Ukrainian", passage: """
            Щоранку я переглядаю новини, відповідаю на кілька повідомлень і планую, що треба встигнути до обіду. \
            Сьогодні ми говоримо про новий проєкт, його терміни та про те, хто за що відповідатиме. \
            Якщо щось незрозуміло, будь ласка, запитуйте. Зазвичай я говорю спокійно, а інколи швидше, коли мене щось захоплює. \
            Числа на кшталт тридцять п'ять, сто і дві тисячі двадцять шість теж мають звучати природно. \
            А тепер розповім, що я думаю про наступні кроки.
            """),
        Language(code: "ru", name: "Russian", passage: """
            Каждое утро я просматриваю новости, отвечаю на несколько сообщений и планирую, что нужно успеть до обеда. \
            Сегодня мы говорим о новом проекте, его сроках и о том, кто за что отвечает. \
            Если что-то непонятно, пожалуйста, спрашивайте. Обычно я говорю спокойно, а иногда быстрее, когда меня что-то увлекает. \
            Числа вроде тридцати пяти, ста и двух тысяч двадцати шести тоже должны звучать естественно. \
            А теперь расскажу, что я думаю о следующих шагах.
            """),
    ]

    /// The languages to offer: the ones the person has switched on, in our order. All of them if none match.
    public static func offered(enabled: [String]) -> [Language] {
        let chosen = all.filter { enabled.contains($0.code) }
        return chosen.isEmpty ? all : chosen
    }

    /// The remembered choice if it is still offered, otherwise the first language offered.
    public static func pick(_ remembered: String?, from offered: [Language]) -> Language {
        offered.first { $0.code == remembered } ?? offered[0]
    }
}
