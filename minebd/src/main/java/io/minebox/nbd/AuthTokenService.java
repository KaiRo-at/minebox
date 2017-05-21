package io.minebox.nbd;

import java.time.Instant;

import com.google.common.base.Charsets;
import com.google.inject.Inject;
import com.google.inject.name.Named;
import com.mashape.unirest.http.HttpResponse;
import com.mashape.unirest.http.Unirest;
import com.mashape.unirest.http.exceptions.UnirestException;
import io.minebox.nbd.encryption.EncyptionKeyProvider;
import org.bitcoinj.core.ECKey;
import org.bitcoinj.core.Sha256Hash;

/**
 * Created by andreas on 21.05.17.
 */
public class AuthTokenService {
    final private EncyptionKeyProvider encyptionKeyProvider;
    private final String rootPath;

    @Inject
    public AuthTokenService(@Named("httpMetadata") String rootPath, EncyptionKeyProvider encyptionKeyProvider) {
        this.rootPath = rootPath;
        this.encyptionKeyProvider = encyptionKeyProvider;
    }

    public String getToken() {
        final String key = encyptionKeyProvider.getImmediatePassword();
        final String s = key + "meta";
        final ECKey privKey = ECKey.fromPrivate(Sha256Hash.twiceOf(s.getBytes(Charsets.UTF_8)).getBytes());

/*
        @POST
        @Path("/token")
        @Produces(MediaType.APPLICATION_OCTET_STREAM)
        public Response createToken(@QueryParam("timestamp") Long nonce, @QueryParam("signature") String signature) {
*/

//        }
        final long timeStamp = Instant.now().toEpochMilli();
        try {
            final HttpResponse<String> token = Unirest.post(rootPath + "auth/token")
                    .queryString("timestamp", timeStamp)
                    .queryString("signature", privKey.signMessage(String.valueOf(timeStamp)))
                    .asString();
            return token.getBody();
        } catch (UnirestException e) {
            throw new RuntimeException(e);
        }

    }

}