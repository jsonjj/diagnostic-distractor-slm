#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>

extern "C" __attribute__((visibility("default")))
void WaylineSpeakUtf8(const char *utf8Text)
{
    if (utf8Text == nullptr)
        return;

    NSString *text = [NSString stringWithUTF8String:utf8Text];
    if (text == nil || text.length == 0)
        return;

    dispatch_async(dispatch_get_main_queue(), ^{
        static AVSpeechSynthesizer *synthesizer = nil;
        if (synthesizer == nil)
            synthesizer = [[AVSpeechSynthesizer alloc] init];
        if (synthesizer.speaking)
            [synthesizer stopSpeakingAtBoundary:AVSpeechBoundaryImmediate];
        AVSpeechUtterance *utterance =
            [AVSpeechUtterance speechUtteranceWithString:text];
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate;
        [synthesizer speakUtterance:utterance];
    });
}
